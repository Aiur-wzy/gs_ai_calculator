from __future__ import annotations

import io
import json
from types import SimpleNamespace

from genshin_app.agent_chat.native_dsh_process_diagnostics import (
    NativeDshProcessDiagnostics,
)


def test_only_bounded_stable_codes_and_exit_survive(tmp_path):
    diagnostics = NativeDshProcessDiagnostics(tmp_path)
    stream = io.BytesIO(b"sk-secret-never-store\n" + b"x" * 20000 + b"\n"
        + b"native_dsh_runtime_failed\nEACCES\n" + b"C:/Users/private/file\n")
    diagnostics.drain(stream, SimpleNamespace(wait=lambda: 17))
    row = json.loads((tmp_path / "last-process-status.json").read_text(encoding="utf-8"))
    assert row == diagnostics.snapshot()
    assert [item["code"] for item in row["codes"]] == ["native_dsh_runtime_failed", "EACCES"]
    assert row["exit_code"] == 17
    assert "secret" not in json.dumps(row) and "private" not in json.dumps(row)


def test_process_diagnostics_ring_and_partial_line_are_bounded():
    diagnostics = NativeDshProcessDiagnostics()
    stream = io.BytesIO(b"dsh_error_code\n" * 100 + b"native_dsh_last_failure")
    diagnostics.drain(stream, SimpleNamespace(wait=lambda: 1))
    assert len(diagnostics.snapshot()["codes"]) == 64
    assert diagnostics.snapshot()["codes"][-1]["code"] == "native_dsh_last_failure"


def test_exit_callback_runs_after_exit_evidence_is_committed():
    diagnostics = NativeDshProcessDiagnostics()
    observed = []
    diagnostics.drain(io.BytesIO(b""), SimpleNamespace(wait=lambda: 2),
                      on_exit=lambda code: observed.append((code, diagnostics.snapshot()["exit_code"])))
    assert observed == [(2, 2)]


def test_exit_notification_closes_only_connecting_carrier():
    from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
    process = NativeDshMuxProcess.__new__(NativeDshMuxProcess)
    closed = []
    process._server = SimpleNamespace(close=lambda: closed.append(True))
    process._connecting = True
    process._startup_process_exited(1)
    assert closed == [True]
    process._connecting = False
    process._startup_process_exited(0)
    assert closed == [True]
