"""Production process and exact offline seam share the same reviewed Node argv."""
from types import SimpleNamespace

from genshin_app.agent_chat.native_runtime_command import native_runtime_command
from genshin_app.frozen_selfcheck_isolation import production_selfcheck_command
from genshin_app.frozen_selfcheck_runtime import offline_production_provider


def test_fixed_flags_are_in_both_ordinary_and_isolated_commands(tmp_path):
    runtime = tmp_path / "runtime"
    root = tmp_path / "sandbox"
    data = root / "data"
    assets = tmp_path / "assets"
    entry = runtime / "src/native-dsh-runtime-entry.mjs"
    ordinary, isolated = production_selfcheck_command(root=root, data_dir=data,
        runtime_root=runtime, assets_root=assets, preload=runtime / "src/native-dsh-selfcheck-entry.mjs")
    assert ordinary == native_runtime_command(runtime / "node.exe", entry, (
        str(data / "native_agent_sessions_v2/dsh-production-v1"),
        str(data / "native_agent_sessions_v2/dsh-file-preview"), str(assets)))
    assert ordinary[:3] == (str(runtime / "node.exe"), "--expose-internals", str(entry))
    assert isolated[:2] == (ordinary[0], "--import")
    assert isolated[3:] == ordinary[1:]


def test_offline_guard_accepts_current_exact_command_and_preserves_real_entry(tmp_path, monkeypatch):
    import json
    import subprocess
    root = tmp_path / "sandbox"
    root.mkdir()
    runtime = tmp_path / "runtime"
    assets = tmp_path / "assets"
    actual = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: actual.append((argv, kwargs)) or SimpleNamespace())
    ordinary, isolated = production_selfcheck_command(root=root, data_dir=root / "data", runtime_root=runtime,
        assets_root=assets, preload=runtime / "src/native-dsh-selfcheck-entry.mjs")
    with offline_production_provider(root=root, data_dir=root / "data", runtime_root=runtime,
            assets_root=assets, package_root=tmp_path) as evidence:
        subprocess.Popen(list(ordinary), cwd=runtime, env={})
    assert len(actual) == len(evidence["launches"]) == 1
    assert actual[0][0] == list(isolated)
    marker = json.loads((root / "production-provider-context.json").read_text(encoding="utf-8"))
    assert marker["main_entry"] == str(runtime / "src/native-dsh-runtime-entry.mjs")
    assert evidence["main_entry"] == marker["main_entry"]
