from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat.native_error_codes import reviewed_native_error_code
from genshin_app.agent_chat.native_error_diagnostics import (
    CONTRACT,
    DIAGNOSTIC_FILE,
    NativeErrorDiagnosticManager,
)
from genshin_app.equipment.schema import PlanError


class DiagnosticError(RuntimeError):
    """Independent boundary fixture; no historical runtime owner required."""

    def __init__(self, code, message, *, cause_code=None):
        super().__init__(message)
        self.code = code
        self.cause_code = cause_code


@pytest.mark.parametrize("cause", [
    "runtime_official_turn_internal_app_tool_checkpoint", "bridge_wire_eof",
    "bridge_wire_truncated", "heartbeat_timeout", "pipe_cancel_settle_timeout",
    "runtime_heartbeat_failed", "runtime_turn_bridge_closed",
])
def test_pure_error_projection_preserves_reviewed_transport_and_operation_cause(cause):
    error = DiagnosticError("runtime_official_turn_failed", "private", cause_code=cause)
    assert reviewed_native_error_code(error, fallback="fallback") == (
        f"runtime_official_turn_failed:{cause}")


def test_diagnostic_records_only_stable_mode_stage_time_and_error_code(tmp_path):
    secret = r"sk-secret C:\Users\private\prompt.txt https://private.test?q=query"

    class Manager:
        @staticmethod
        def connect(_request):
            raise PlanError(
                secret,
                "native_runtime_start_failed",
                {"stage": "runtime_start", "cause": "pipe_connect_timeout"},
            )

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="native_agent_development",
        diagnostics_directory=tmp_path / "logs",
    )
    with pytest.raises(PlanError):
        manager.connect({"api_key": secret, "prompt": secret})

    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert set(record) == {
        "contract",
        "recorded_at",
        "mode",
        "stage",
        "error_code",
    }
    assert record["contract"] == CONTRACT
    assert record["mode"] == "native_agent_development"
    assert record["stage"] == "connect"
    assert record["error_code"] == (
        "native_runtime_start_failed:pipe_connect_timeout"
    )
    serialized = json.dumps(record).casefold()
    for fragment in ("secret", "users", "prompt", "https", "query", "private"):
        assert fragment not in serialized


def test_diagnostic_drops_unreviewed_codes_and_never_changes_original_error(tmp_path):
    class UnsafeError(RuntimeError):
        reason = r"C:\private\sk-secret"

    error = UnsafeError("prompt and reasoning")

    class Manager:
        @staticmethod
        def send(*_args):
            raise error

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    with pytest.raises(UnsafeError) as caught:
        manager.send("conversation", "prompt", "request")

    assert caught.value is error
    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["error_code"] == "native_agent_operation_failed"
    assert record["stage"] == "send"
    assert "private" not in json.dumps(record).casefold()


def test_successful_operation_does_not_create_a_diagnostic(tmp_path):
    class Manager:
        @staticmethod
        def poll(session_id, after_seq=0):
            return {"session_id": session_id, "next_seq": after_seq}

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    assert manager.poll("conversation", 3)["next_seq"] == 3
    assert not (tmp_path / "logs" / DIAGNOSTIC_FILE).exists()


def test_projected_turn_error_is_recorded_without_message_or_other_payload(tmp_path):
    class Manager:
        @staticmethod
        def poll(_session_id, _after_seq=0):
            return {
                "events": [{
                    "seq": 4,
                    "type": "error",
                    "reason": "runtime_official_turn_failed:pipe_read_timeout",
                    "message": r"secret prompt C:\private\file",
                }],
            }

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    assert manager.poll("conversation", 3)["events"][0]["seq"] == 4
    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["stage"] == "turn"
    assert record["error_code"] == (
        "runtime_official_turn_failed:pipe_read_timeout"
    )
    assert "secret" not in json.dumps(record).casefold()


def test_wait_events_projected_error_is_recorded(tmp_path):
    class Manager:
        @staticmethod
        def wait_events(*_args):
            return {
                "events": [{
                    "seq": 5,
                    "type": "error",
                    "reason": "provider_stream_closed",
                }],
            }

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="native_agent_development",
        diagnostics_directory=tmp_path / "logs",
    )
    assert manager.wait_events("conversation")["events"][0]["seq"] == 5

    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["stage"] == "turn"
    assert record["error_code"] == "provider_stream_closed"


def test_reviewed_runtime_turn_cause_is_retained_without_exception_text(tmp_path):
    secret = r"provider payload https://private.test C:\private\key"

    class Manager:
        @staticmethod
        def send(*_args):
            raise DiagnosticError(
                "runtime_official_turn_failed",
                secret,
                cause_code="pipe_read_timeout",
            )

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    with pytest.raises(DiagnosticError):
        manager.send("conversation", secret)

    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["stage"] == "send"
    assert record["error_code"] == (
        "runtime_official_turn_failed:pipe_read_timeout"
    )
    assert "private" not in json.dumps(record).casefold()


def test_reviewed_catalog_rollover_cause_is_retained_without_exception_text(tmp_path):
    secret = r"provider payload https://private.test C:\private\catalog.json"

    class Manager:
        @staticmethod
        def send(*_args):
            raise DiagnosticError(
                "runtime_official_turn_failed",
                secret,
                cause_code="turn_app_tool_catalog_dispose_failed",
            )

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    with pytest.raises(DiagnosticError):
        manager.send("conversation", secret)

    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["error_code"] == (
        "runtime_official_turn_failed:turn_app_tool_catalog_dispose_failed"
    )
    serialized = json.dumps(record).casefold()
    for fragment in ("provider", "private", "catalog.json", "https"):
        assert fragment not in serialized


def test_unreviewed_runtime_turn_cause_is_not_reflected(tmp_path):
    class Manager:
        @staticmethod
        def send(*_args):
            raise DiagnosticError(
                "runtime_official_turn_failed",
                "sanitized",
                cause_code=r"C:\private\key",
            )

    manager = NativeErrorDiagnosticManager(
        Manager(),
        mode="release",
        diagnostics_directory=tmp_path / "logs",
    )
    with pytest.raises(DiagnosticError):
        manager.send("conversation")

    record = json.loads(
        (tmp_path / "logs" / DIAGNOSTIC_FILE).read_text(encoding="utf-8"),
    )
    assert record["error_code"] == "runtime_official_turn_failed"
    assert "private" not in json.dumps(record).casefold()


def test_unknown_mode_is_rejected_before_a_manager_can_run(tmp_path):
    with pytest.raises(ValueError, match="unsupported native diagnostic mode"):
        NativeErrorDiagnosticManager(
            object(),
            mode="arbitrary",
            diagnostics_directory=tmp_path,
        )
