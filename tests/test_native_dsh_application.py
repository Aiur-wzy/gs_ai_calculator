from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.credential_generation import CredentialGenerationController
from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.gui.api import GuiApi


class Store:
    available = True

    def __init__(self):
        self.value = (0, None)
        self.lock = threading.Lock()

    def read_versioned(self):
        return self.value

    def compare_and_swap(self, *, expected_generation, next_generation, secret):
        with self.lock:
            if self.value[0] != expected_generation:
                return False
            self.value = next_generation, secret
            return True


class Client:
    def __init__(self):
        self.calls = []
        self.closed = False

    def control(self, operation, payload=None):
        payload = payload or {}
        # Deliberately retain only safe evidence, not the injected secret.
        self.calls.append((operation, {key: value for key, value in payload.items() if key != "secret"}))
        if operation.startswith("dsh_secret_"):
            return {"credential_generation": payload["credential_generation"],
                    "configured": operation == "dsh_secret_set"}
        return {"state": "drained" if operation == "dsh_runtime_drain" else
                "stopping" if operation == "dsh_runtime_shutdown" else "healthy"}

    def wait_domain_idle(self):
        self.calls.append(("wait_domain_idle", {}))

    def boot(self):
        return {"injections": []}

    def bundle(self, _):
        raise AssertionError

    def close(self):
        self.closed = True


def test_application_stays_idle_and_injects_credentials_only_over_control():
    credentials = CredentialGenerationController(Store())
    credentials.save("sk-test-sentinel", expected_generation=0)
    client = Client()
    closed = []
    application = NativeDshApplication(SimpleNamespace(client=client, close=lambda: closed.append(True)),
                                       credentials=credentials)
    assert client.calls == [("dsh_secret_set", {"provider": "deepseek-official", "credential_generation": 1})]
    assert application.runtime_status() == {"state": "healthy"}
    assert "sentinel" not in repr(application.runtime_status())
    assert application.session_status("exact-session") == {"state": "healthy"}
    assert client.calls[-1] == ("dsh_runtime_session_status", {"session_id": "exact-session"})
    application.close()
    application.close()
    assert client.closed and closed == [True]
    assert client.calls[-1][0] == "dsh_runtime_shutdown"
    assert [row[0] for row in client.calls[-3:]] == ["dsh_runtime_drain", "wait_domain_idle", "dsh_runtime_shutdown"]
    assert application.shutdown_diagnostic() == {"state": "drained_and_stopped"}
    with pytest.raises(NativeDshMuxError, match="dsh_shell_closed"):
        application.session_status("exact-session")


def test_credential_settings_do_not_start_runtime_and_drop_input_key(tmp_path):
    credentials = CredentialGenerationController(Store())
    def forbidden(_):
        raise AssertionError("no runtime for Windows credential settings")
    api = GuiApi(tmp_path, dsh_shell_factory=forbidden, dsh_credentials=credentials)
    assert api.dsh_client_credential_status()["credential_generation"] == 0
    request = {"api_key": "sk-test-sentinel", "expected_generation": 0}
    saved = api.dsh_client_credential_save(request)
    assert "api_key" not in request
    assert saved["saved"] and saved["runtime_sync"]["state"] == "pending_runtime"
    assert api.dsh_client_runtime_status() == {"state": "stopped"}
    assert "sentinel" not in repr(saved)
    assert not api.dsh_client_credential_delete({"expected_generation": 1})["saved"]


def test_credential_cas_conflict_cannot_change_saved_key(tmp_path):
    credentials = CredentialGenerationController(Store())
    credentials.save("sk-test-first", expected_generation=0)
    api = GuiApi(tmp_path, dsh_credentials=credentials)
    request = {"api_key": "sk-test-second", "expected_generation": 0}
    with pytest.raises(Exception, match="credential_generation_conflict"):
        api.dsh_client_credential_save(request)
    assert "api_key" not in request
    assert credentials.resolve_for_provider() == ("sk-test-first", 1)


def test_failed_runtime_sync_does_not_misreport_successful_windows_save(tmp_path):
    credentials = CredentialGenerationController(Store())
    api = GuiApi(tmp_path, dsh_credentials=credentials)
    def disconnected():
        raise NativeDshMuxError("dsh_mux_connection_lost")
    api._dsh_shell = SimpleNamespace(synchronize_credentials=disconnected)
    result = api.dsh_client_credential_save({"api_key": "sk-test-new", "expected_generation": 0})
    assert result["saved"] and result["credential_generation"] == 1
    assert result["runtime_sync"] == {"state": "failed", "code": "dsh_mux_connection_lost"}


def test_closed_process_is_cleaned_even_without_shutdown_ack():
    credentials = CredentialGenerationController(Store())
    client = Client()
    closed = []
    app = NativeDshApplication(SimpleNamespace(client=client, close=lambda: closed.append(True)),
                               credentials=credentials)
    def unavailable(*_):
        raise NativeDshMuxError("dsh_mux_connection_lost")
    client.control = unavailable
    app.close()
    assert closed == [True] and client.closed


def test_startup_cleanup_failure_preserves_original_failure():
    client = Client()
    def failed_control(*_args):
        raise NativeDshMuxError("dsh_original_startup_failure")
    def failed_close():
        raise RuntimeError("cleanup failed")
    client.control = failed_control
    with pytest.raises(NativeDshMuxError, match="dsh_original_startup_failure"):
        NativeDshApplication(SimpleNamespace(client=client, close=failed_close),
            credentials=CredentialGenerationController(Store()))


def test_runtime_status_reports_actual_process_failure_without_raw_stderr():
    credentials = CredentialGenerationController(Store())
    client = Client()
    evidence = {"exit_code": None, "codes": [], "persistence_failed": False}
    process = SimpleNamespace(client=client, close=lambda: None,
                              diagnostics=SimpleNamespace(snapshot=lambda: dict(evidence)))
    app = NativeDshApplication(process, credentials=credentials)
    assert app.runtime_status() == {"state": "healthy"}
    evidence.update(exit_code=1, codes=[{"code": "native_dsh_runtime_failed", "at": "fixture"}])
    failed = app.runtime_status()
    assert failed["state"] == "failed" and failed["code"] == "native_dsh_runtime_failed"
    assert "stderr" not in failed and "secret" not in repr(failed)
    app.close()
