"""The fixed QA transport cannot select a different production command."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import native_provider_proxy
from genshin_app.frozen_selfcheck_isolation import (
    SelfcheckError,
    production_selfcheck_command,
)
from genshin_app.frozen_selfcheck_runtime import (
    engineering_credentials,
    offline_production_provider,
    verify_fixed_tool_settlements,
)


def _paths(tmp_path):
    runtime = tmp_path / "package/agent-runtime"
    assets = tmp_path / "package/frontend"
    root = tmp_path / "sandbox"
    root.mkdir()
    return {"root": root, "data_dir": root / "data", "runtime_root": runtime,
            "assets_root": assets, "package_root": tmp_path / "package"}


@pytest.mark.parametrize("mutation", ["node", "entry", "profile", "flags", "extra", "cwd", "node_options", "shell", "executable",
                                     "missing_environment", "null_environment", "string_environment", "mixed_node_options"])
def test_offline_seam_rejects_near_match_without_starting_any_process(tmp_path, monkeypatch, mutation):
    paths = _paths(tmp_path)
    ordinary, _ = production_selfcheck_command(**{key: paths[key] for key in (
        "root", "data_dir", "runtime_root", "assets_root")},
        preload=paths["runtime_root"] / "src/native-dsh-selfcheck-entry.mjs")
    command = list(ordinary)
    if mutation in {"node", "entry", "profile"}:
        target = {"node": paths["runtime_root"] / "node.exe",
                  "entry": paths["runtime_root"] / "src/native-dsh-runtime-entry.mjs",
                  "profile": paths["data_dir"] / "native_agent_sessions_v2/dsh-production-v1"}[mutation]
        command[command.index(str(target))] += ".unexpected"
    if mutation == "flags":
        command.remove("--expose-internals")
    if mutation == "extra":
        command.append("--extra")
    options = {"cwd": paths["runtime_root"], "env": {}}
    if mutation == "cwd":
        options["cwd"] = paths["root"]
    if mutation == "node_options":
        options["env"] = {"NODE_OPTIONS": "--import unexpected"}
    if mutation == "mixed_node_options":
        options["env"] = {"NoDe_OpTiOnS": "--import unexpected"}
    if mutation == "shell":
        options["shell"] = True
    if mutation == "executable":
        options["executable"] = str(paths["root"] / "other.exe")
    if mutation == "missing_environment":
        options.pop("env")
    if mutation == "null_environment":
        options["env"] = None
    if mutation == "string_environment":
        options["env"] = "unreviewed"
    original = lambda *_args, **_kwargs: pytest.fail("unexpected process started")
    monkeypatch.setattr(subprocess, "Popen", original)
    with pytest.raises(SelfcheckError, match="selfcheck_(unexpected_process_command|node_options_forbidden|process_environment_invalid)"), \
            offline_production_provider(**paths):
        subprocess.Popen(command, **options)
    assert subprocess.Popen is original


def test_offline_seam_rejects_positional_popen_overrides_before_original_call(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    ordinary, _ = production_selfcheck_command(**{key: paths[key] for key in (
        "root", "data_dir", "runtime_root", "assets_root")},
        preload=paths["runtime_root"] / "src/native-dsh-selfcheck-entry.mjs")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("unexpected process started"))
    with pytest.raises(SelfcheckError, match="selfcheck_unexpected_process_command"), \
            offline_production_provider(**paths):
        subprocess.Popen(list(ordinary), 42, cwd=paths["runtime_root"], env={})


def test_offline_seam_preserves_primary_factory_error_and_restores_config_ports(tmp_path):
    paths = _paths(tmp_path)
    original, proxy = subprocess.Popen, native_provider_proxy.read_windows_native_provider_proxy_or_direct
    original_error = RuntimeError("primary_release_verification_failure")
    with pytest.raises(RuntimeError) as error, offline_production_provider(**paths):
        assert native_provider_proxy.read_windows_native_provider_proxy_or_direct() == {}
        raise original_error
    assert error.value is original_error
    assert subprocess.Popen is original
    assert native_provider_proxy.read_windows_native_provider_proxy_or_direct is proxy


def test_engineering_secret_uses_real_generation_controller_without_os_store():
    credentials = engineering_credentials()
    assert credentials.status()["credential_generation"] == 1
    assert credentials.status()["available"] is True
    secret, generation = credentials.resolve_for_provider()
    assert secret == "offline-selfcheck-public-engineering-input" and generation == 1


@pytest.mark.parametrize("scenario", ["complete", "missing", "extra", "wrong_tool", "failed", "nonzero", "inflight"])
def test_fixed_qa_requires_complete_successful_actual_durable_call_set(tmp_path, scenario):
    from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
    from tests.test_native_app_call_ledger import HASH, identity

    store = NativeAppCallLedger(tmp_path / "native-dsh-workspaces/conversation-1", conversation_id="conversation-1")
    count = 1 if scenario == "missing" else 3 if scenario == "extra" else 2
    for index in range(count):
        current = identity(call_id=f"call-{index}")
        if scenario == "wrong_tool" and index == 0:
            current["tool_name"] = "gs_read_skill"
        store.begin(identity=current, effect="none", before_state_hash=HASH)
        if scenario == "inflight" and index == count - 1:
            continue
        store.settle(identity=current, effect="none", after_state_hash=HASH,
            state="settled_failure" if scenario == "failed" else "settled_success",
            result={"code": 1 if scenario in {"failed", "nonzero"} else 0})
    calls = [{"tool": "gs_read_context"}] * 2
    if scenario == "complete":
        assert verify_fixed_tool_settlements(tmp_path, "conversation-1", calls)["tool_counts"] == {"gs_read_context": 2}
    else:
        with pytest.raises(SelfcheckError, match="selfcheck_fixed_tool_settlements_incomplete"):
            verify_fixed_tool_settlements(tmp_path, "conversation-1", calls)


@pytest.mark.parametrize("explicit", [False, True])
def test_offline_factory_redirects_user_caches_and_does_not_inherit_execution_hooks(
    tmp_path, monkeypatch, explicit,
):
    from genshin_app.agent_chat import native_dsh_production

    paths = _paths(tmp_path)
    outside = {
        "LOCALAPPDATA": "C:/outside/native-cache", "APPDATA": "C:/outside/roaming",
        "USERPROFILE": "C:/outside/user", "HOME": "C:/outside/home",
        "TEMP": "C:/outside/temp", "TMP": "C:/outside/tmp", "PATH": "C:/outside/tools",
        "PROGRAMFILES": "C:/outside/programs", "PSMODULEPATH": "C:/outside/modules",
        "DEEPSEEK_API_KEY": "never-inherit-key", "NODE_OPTIONS": "never-import-hook",
        "PYTHONPATH": "never-import-python", "DSH_HOME": "C:/outside/dsh",
    }
    for key, value in outside.items():
        monkeypatch.setenv(key, value)
    captured = {}

    def capture_before_process(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("captured_real_factory_arguments_without_starting_process")

    monkeypatch.setattr(native_dsh_production, "create_native_dsh_application", capture_before_process)
    with pytest.raises(RuntimeError, match="captured_real_factory_arguments"), offline_production_provider(**paths):
        native_dsh_production.create_native_dsh_application(
            **({"runtime_environment": outside} if explicit else {}))
    environment = captured["runtime_environment"]
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "TEMP", "TMP"):
        assert Path(environment[key]).is_relative_to(paths["root"])
        assert environment[key] != outside[key]
    assert not {"PATH", "PROGRAMFILES", "PSMODULEPATH", "DEEPSEEK_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "DSH_HOME"}.intersection(environment)
    assert native_dsh_production.create_native_dsh_application is capture_before_process
