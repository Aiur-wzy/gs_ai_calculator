"""Real local CLI + private pipe + official approval cancellation boundaries.

Provider and human decisions are explicit offline fixtures. No window, EXE,
real API credential or synthetic DSH Turn is involved.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.credential_generation import CredentialGenerationController
from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_native_dsh_application import Store
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit offline CLI/private-pipe cancellation qualification, no GUI",
)


@pytest.mark.parametrize("mode", ["cancel_rpc", "close_app"])
def test_local_cli_approval_cancellation_cannot_write_or_block_shutdown(tmp_path, db_path, mode):
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    script = tmp_path / "provider.json"
    script.write_text(json.dumps({"repeatCalls": False, "calls": [{"tool": "ask_user_question", "arguments": {
        "questions": [{"id": "hold-open-turn", "question": "隔离测试：等待配置操作后再继续。",
            "options": [{"label": "继续", "description": "本夹具不回答，保持真实Turn等待"}]}]}}]}), encoding="utf-8")
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-ui-projection-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror), str(ROOT / "frontend"), str(script)),
        domain_handler=NativeDshHostRequests(registry, data_dir=tmp_path),
        diagnostics_root=tmp_path / "process-diagnostics")
    credentials = CredentialGenerationController(Store())
    credentials.save("offline-config-cancel-fixture-not-a-real-key", expected_generation=0)
    application = NativeDshApplication(process, credentials=credentials)
    registry.enable_cli_link(settings_remote=application.dsh_client_rpc,
        settings_cancel=application.dsh_client_request_cancel)
    application.attach_cli_retirement(registry.close_cli_link)
    sid, stream_id = "config-cancel-" + mode, str(uuid.uuid4())
    frames, frame_errors = queue.Queue(), []
    closed = threading.Event()
    cli = None
    pump = None

    def rpc(endpoint, args):
        result = application.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api",
            "endpoint": endpoint, "payload": {"args": args}})
        assert result["ok"], result
        return result.get("value")

    def next_frame(predicate):
        deadline = time.monotonic() + 60
        observed = []
        while time.monotonic() < deadline:
            try:
                frame = frames.get(timeout=.1)
            except queue.Empty:
                if frame_errors:
                    raise frame_errors[0]
                continue
            observed.append({key: frame[key] for key in ("type", "event", "eventId") if key in frame})
            if predicate(frame):
                return frame
        pytest.fail(f"Expected official event not observed: {observed}")

    def cli_command(*args):
        return [sys.executable, "-m", "genshin_app", "--data-dir", str(tmp_path), "agent", "dsh-settings", *args]

    try:
        registry.prepare_document(sid, None, None)
        descriptor = registry.cli_connection(sid)
        application.dsh_client_stream_open({"stream_id": stream_id, "channel": "/api", "endpoint": "$events",
            "payload": {"args": {}}, "has_uplink": False})

        def read_events():
            cursor = 0
            try:
                while not closed.is_set():
                    batch = application.dsh_client_stream_read({"stream_id": stream_id, "cursor": cursor})
                    cursor = batch["cursor"]
                    for frame in batch["items"]:
                        frames.put(frame)
                    if batch["done"]:
                        return
            except Exception as error:  # noqa: BLE001 - test records only transport shutdown
                if not closed.is_set():
                    frame_errors.append(error)

        pump = threading.Thread(target=read_events, daemon=True)
        pump.start()
        ready = next_frame(lambda frame: frame.get("type") == "ready")
        rpc("session/create", {"request": {"sessionId": sid}})
        rpc("session/prompt", {"request": {"sessionId": sid, "requestId": str(uuid.uuid4()),
            "mode": "queue", "content": [{"type": "text", "text": "Offline CLI approval cancellation fixture"}]}})
        next_frame(lambda frame: frame.get("type") == "waterfall" and frame.get("event") == "user-questions/request")
        assert application.session_status(sid)["agent_running"] is True
        shown = subprocess.run(cli_command("show", "--connection", descriptor, "--entry", "compaction-basic"),
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert shown.returncode == 0, shown.stderr + shown.stdout
        before = json.loads(shown.stdout)["data"]["entries"][0]
        # The official profile patch is initially absent until a first edit.
        # Preserve absence too; do not construct an App-written config mirror.
        config_path = profile / "runtime-profile" / "cordis.patch.yml"
        raw_before = config_path.read_bytes() if config_path.exists() else None
        request = tmp_path / "config-edit.json"
        request.write_text(json.dumps({"entry_id": "compaction-basic", "expected_fingerprint": before["fingerprint"],
            "operations": [{"op": "set", "path": ["thresholdRatio"], "value": .83}]}), encoding="utf-8")
        cli = subprocess.Popen(cli_command("set", "--connection", descriptor, "--input", str(request)), cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        approval = next_frame(lambda frame: frame.get("type") == "waterfall" and frame.get("event") == "approval/request")
        assert approval["request"]["toolName"] == "profile_config_edit"
        assert (config_path.read_bytes() if config_path.exists() else None) == raw_before
        assert cli.poll() is None
        if mode == "cancel_rpc":
            with registry._lock:
                pending = list(registry._cli_settings_calls)
            assert len(pending) == 1
            application.dsh_client_request_cancel({"request_id": pending[0]})
            stdout, stderr = cli.communicate(timeout=30)
            assert cli.returncode != 0, stdout + stderr
            cancelled = next_frame(lambda frame: frame.get("type") == "cancel" and frame.get("eventId") == approval["eventId"])
            assert cancelled["eventId"] == approval["eventId"]
            # A late UI answer to a retired event cannot authorize the edit.
            rpc("$events/result", {"clientId": ready["clientId"], "eventId": approval["eventId"],
                "outcome": {"kind": "result", "value": "allowed-once"}})
            after = rpc("genshinProfile/configDescribe", {"request": {"entry_id": "compaction-basic"}})["entries"][0]
            assert after["fingerprint"] == before["fingerprint"]
            closed.set()
            application.close()
        else:
            closed.set()
            finished = threading.Event()
            def close_app():
                try:
                    application.close()
                finally:
                    finished.set()
            threading.Thread(target=close_app, daemon=True).start()
            assert finished.wait(30), "Official pending configuration approval blocked App shutdown"
            stdout, stderr = cli.communicate(timeout=30)
            assert cli.returncode != 0, stdout + stderr
        assert application.shutdown_diagnostic() == {"state": "drained_and_stopped"}
        terminal = json.loads(stderr)
        assert terminal["reason"] in {"dsh_mux_request_cancelled", "dsh_shell_request_cancelled"}
        assert terminal["category"] == "cancelled"
        assert terminal["recovery"]["retry_same_request"] is False
        assert (config_path.read_bytes() if config_path.exists() else None) == raw_before
        assert not Path(descriptor).exists()
        evidence = {"mode": mode, "status": "passed", "actual_local_cli": True, "actual_private_pipe": True,
            "official_open_turn": True, "official_approval": True, "no_configuration_write": True,
            "late_approval_ignored": True if mode == "cancel_rpc" else None,
            "cli_terminal_reason": terminal["reason"], "shutdown": application.shutdown_diagnostic()}
        (tmp_path / "cancellation-receipt.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    finally:
        closed.set()
        if cli is not None and cli.poll() is None:
            cli.kill()
            cli.communicate(timeout=10)
        # Failed engineering cases must not leak a Node or HTTP listener.
        if application.shutdown_diagnostic() is None:
            process.close()
        application.close()
        registry.close()
        if pump is not None:
            pump.join(timeout=5)
