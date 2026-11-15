"""Fresh production profiles must satisfy Node's fixed-root preconditions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from genshin_app.agent_chat import native_dsh_production as production
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry


def _admitted_factory(monkeypatch, tmp_path):
    options = {}
    monkeypatch.setattr(production, "verify_native_dsh_production", lambda **_kwargs: {
        "manifest": {"entry": "src/native-dsh-runtime-entry.mjs"},
        "manifest_sha256": "a" * 64, "verification_mode": "release",
    })

    def process(**kwargs):
        options.update(kwargs)
        profile, mirror, _assets = map(production.Path, kwargs["entry_args"])
        assert profile.is_dir(), "production profile must exist before Node starts"
        assert mirror.is_dir(), "fixed-root mirror must exist before Node starts"
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(production, "NativeDshMuxProcess", process)
    def unused_rpc(*_args, **_kwargs):
        pytest.fail("path-only fixture must not issue a settings RPC")

    monkeypatch.setattr(production, "NativeDshApplication", lambda *_args, **_kwargs: SimpleNamespace(
        dsh_client_rpc=unused_rpc, dsh_client_request_cancel=unused_rpc,
        attach_cli_retirement=lambda callback: options.update(cli_retirement=callback)))
    workspaces = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=tmp_path / "db")
    monkeypatch.setattr(workspaces, "enable_cli_link", lambda **settings: options.update(cli_link=settings))
    return options, workspaces


def test_fresh_profile_creates_exact_private_mirror_before_starting_node(monkeypatch, tmp_path):
    options, workspaces = _admitted_factory(monkeypatch, tmp_path)
    data = tmp_path / "brand-new-user"
    try:
        application = production.create_native_dsh_application(data, workspaces=workspaces)
        profile, mirror, _assets = map(production.Path, options["entry_args"])
        assert profile == data / "native_agent_sessions_v2/dsh-production-v1"
        assert mirror == data / "native_agent_sessions_v2/dsh-file-preview"
        assert list(mirror.iterdir()) == []
        assert application.release_evidence["manifest_sha256"] == "a" * 64
        assert options["cli_link"]["settings_remote"] == application.dsh_client_rpc
        assert options["cli_link"]["settings_cancel"] == application.dsh_client_request_cancel
        assert options["cli_retirement"] == workspaces.close_cli_link
    finally:
        workspaces.close()


def test_reused_private_mirror_is_not_cleared_during_startup(monkeypatch, tmp_path):
    _options, workspaces = _admitted_factory(monkeypatch, tmp_path)
    data = tmp_path / "existing-user"
    mirror = data / "native_agent_sessions_v2/dsh-file-preview"
    mirror.mkdir(parents=True)
    existing = mirror / "preview.txt"
    existing.write_text("existing preview", encoding="utf-8")
    try:
        production.create_native_dsh_application(data, workspaces=workspaces)
        assert existing.read_text(encoding="utf-8") == "existing preview"
    finally:
        workspaces.close()


def test_provider_dsh_home_is_fixed_before_child_imports(monkeypatch, tmp_path):
    from genshin_app.agent_chat.native_runtime_environment import (
        build_native_runtime_environment,
    )
    options, workspaces = _admitted_factory(monkeypatch, tmp_path)
    data = tmp_path / "isolated-user"
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "ambient-user-home"))
    try:
        production.create_native_dsh_application(data, workspaces=workspaces, runtime_environment={
            "GENSHIN_NATIVE_DSH_PROFILE_ROOT": "relative-injected-profile",
            "DSH_HOME": str(tmp_path / "injected-user-home"), "DEEPSEEK_API_KEY": "forbidden",
        })
        actual_environment = build_native_runtime_environment(options["runtime_environment"])
        expected = data / "native_agent_sessions_v2/dsh-production-v1"
        assert actual_environment["DSH_HOME"] == str(expected.resolve() / "dsh-home")
        assert actual_environment["GENSHIN_NATIVE_DSH_PROFILE_ROOT"] == str(expected.resolve())
        assert "DEEPSEEK_API_KEY" not in actual_environment
    finally:
        workspaces.close()


def test_unadmitted_release_does_not_create_profile_or_start_node(monkeypatch, tmp_path):
    def rejected(**_kwargs):
        raise NativeDshMuxError("dsh_shell_release_pin_required")

    monkeypatch.setattr(production, "verify_native_dsh_production", rejected)
    monkeypatch.setattr(production, "NativeDshMuxProcess", lambda **_kwargs: pytest.fail("process"))
    data = tmp_path / "unadmitted-user"
    with pytest.raises(NativeDshMuxError, match="dsh_shell_release_pin_required"):
        production.create_native_dsh_application(data, workspaces=object())
    assert not data.exists()


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_public_factory_preserves_constructor_failure_when_cleanup_fails(monkeypatch, tmp_path, diagnostic_fails):
    _options, workspaces = _admitted_factory(monkeypatch, tmp_path)
    original = NativeDshMuxError("dsh_original_startup_failure")
    recorded = []
    closed = []

    def record(code):
        recorded.append(code)
        if diagnostic_fails:
            raise OSError("diagnostic unavailable")

    def close():
        closed.append(True)
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(production, "NativeDshMuxProcess", lambda **_kwargs: SimpleNamespace(
        close=close, diagnostics=SimpleNamespace(record_code=record)))

    def failed_application(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(production, "NativeDshApplication", failed_application)
    try:
        with pytest.raises(NativeDshMuxError) as caught:
            production.create_native_dsh_application(tmp_path / "new-user", workspaces=workspaces)
        assert caught.value is original
        assert original.__context__ is None
        assert closed == [True]
        assert recorded == [b"native_dsh_startup_cleanup_failed"]
    finally:
        workspaces.close()
