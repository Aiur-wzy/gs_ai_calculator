"""Source DOM verification keeps the exact process and offline isolation fence."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_runtime_environment import (
    build_native_runtime_environment,
)
from genshin_app.frozen_selfcheck_frontend import (
    prepare_source_frontend_role,
    source_frontend_options_match,
    verify_source_frontend_role,
)
from genshin_app.frozen_selfcheck_isolation import (
    SelfcheckError,
    production_selfcheck_command,
)
from genshin_app.frozen_selfcheck_runtime import offline_production_provider
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]


def role_options(role):
    # Match apply_frontend_document's OS-only environment, without DSH flags.
    environment = {key: value for key, value in build_native_runtime_environment().items()
        if key in {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC"}}
    return {"cwd": role["cwd"], "env": environment,
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "text": True, "encoding": "utf-8", "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


@pytest.mark.parametrize("mutation", ["node", "entry", "extra_arg", "cwd", "text", "encoding", "stdin",
    "stdout", "stderr", "shell", "executable", "env_key", "env_secret", "missing_option"])
def test_source_role_rejects_every_nonfixed_process_option(tmp_path, mutation):
    root = tmp_path / "sandbox"
    root.mkdir()
    role = prepare_source_frontend_role(root=root, runtime_root=ROOT / "agent-runtime", package_root=tmp_path / "package")
    command, options = list(role["ordinary"]), role_options(role)
    assert source_frontend_options_match(role, command, (), options)
    if mutation in {"node", "entry"}:
        command[0 if mutation == "node" else 1] += ".unexpected"
    elif mutation == "extra_arg":
        command.append("--extra")
    elif mutation == "cwd":
        options["cwd"] = root
    elif mutation in {"text", "stdin", "stdout", "stderr"}:
        options[mutation] = False
    elif mutation == "encoding":
        options["encoding"] = "utf-16"
    elif mutation in {"shell", "executable"}:
        options[mutation] = True
    elif mutation.startswith("env_"):
        options["env"]["NODE_OPTIONS" if mutation == "env_key" else "DEEPSEEK_API_KEY"] = "fixture-deny"
    else:
        options.pop("encoding")
    assert not source_frontend_options_match(role, command, (), options)


@pytest.mark.parametrize("behavior", ["clean", "network", "process", "outside_write", "context_tamper"])
def test_actual_generated_preload_keeps_network_process_and_file_denials(tmp_path, behavior):
    node = pinned_native_node()
    root, package = tmp_path / "sandbox space+#", tmp_path / "package"
    root.mkdir()
    entry = package / "frontend/scripts/app-document-projection.mjs"
    entry.parent.mkdir(parents=True)
    scripts = {
        "clean": "process.stdout.write('ok');",
        "network": "await fetch('http://127.0.0.1:9/never-contacted');",
        "process": "import cp from 'node:child_process';cp.spawn(process.execPath,['--version']);",
        "outside_write": "import fs from 'node:fs';fs.writeFileSync(" + json.dumps(str(tmp_path / "outside.txt")) + ",'never');",
        "context_tamper": "process.stdout.write('must-not-run');",
    }
    entry.write_text(scripts[behavior], encoding="utf-8")
    # This uses the actual installed guard module, not a replacement guard.
    role = prepare_source_frontend_role(root=root, runtime_root=node.parent, package_root=package)
    if behavior == "context_tamper":
        marker = root / "frontend-provider-context.json"
        altered = json.loads(marker.read_text(encoding="utf-8"))
        altered["package_root"] = str(tmp_path)
        marker.write_text(json.dumps(altered), encoding="utf-8")
    process = subprocess.run(role["actual"], cwd=package, capture_output=True, text=True, encoding="utf-8",
        env=build_native_runtime_environment(), timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert not (tmp_path / "outside.txt").exists()
    if behavior == "clean":
        assert process.returncode == 0, process.stderr
        assert process.stdout == "ok"
        isolated = verify_source_frontend_role(role)
        assert isolated["counters"]["normal"] == {}
        assert isolated["counters"]["negative"]["network"] == 1
    elif behavior == "context_tamper":
        assert process.returncode != 0 and "must-not-run" not in process.stdout
        assert "selfcheck_frontend_context_invalid" in process.stderr
    else:
        assert process.returncode != 0
        isolated = json.loads(role["report"].read_text(encoding="utf-8"))
        assert isolated["counters"]["normal"][behavior] == 1
        with pytest.raises(SelfcheckError, match="selfcheck_frontend_isolation_invalid"):
            verify_source_frontend_role(role)


def test_exact_frontend_role_cannot_launch_twice_or_replace_frozen_host_scope(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    runtime, package = ROOT / "agent-runtime", tmp_path / "package"
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **options: calls.append((argv, options)))
    ordinary, _ = production_selfcheck_command(root=root, data_dir=root / "data", runtime_root=runtime,
        assets_root=package / "frontend", preload=runtime / "src/native-dsh-selfcheck-entry.mjs")
    role = {"ordinary": (str(runtime / "node.exe"), str(package / "frontend/scripts/app-document-projection.mjs")),
            "cwd": package}
    with pytest.raises(SelfcheckError, match="selfcheck_frontend_launch_count_invalid"), offline_production_provider(
            root=root, data_dir=root / "data", runtime_root=runtime, assets_root=package / "frontend", package_root=package):
        subprocess.Popen(list(ordinary), cwd=runtime, env={})
        subprocess.Popen(list(role["ordinary"]), **role_options(role))
        subprocess.Popen(list(role["ordinary"]), **role_options(role))
    assert len(calls) == 2
    assert calls[1][0][1] == "--import"
    frozen_root = tmp_path / "frozen-sandbox"
    frozen_root.mkdir()
    monkeypatch.setattr("genshin_app.frozen_selfcheck_runtime.is_frozen", lambda: True)
    before = copy.deepcopy(calls)
    with pytest.raises(SelfcheckError, match="selfcheck_unexpected_process_command"), offline_production_provider(
            root=frozen_root, data_dir=frozen_root / "data", runtime_root=runtime,
            assets_root=package / "frontend", package_root=package):
        subprocess.Popen(list(role["ordinary"]), **role_options(role))
    assert calls == before


@pytest.mark.parametrize("payload", [[], {}, {"counters": {"normal": {"network": None}}}])
def test_malformed_isolation_file_is_not_a_clean_role_receipt(tmp_path, payload):
    report = tmp_path / "frontend-node-isolation.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SelfcheckError, match="selfcheck_frontend_isolation_invalid"):
        verify_source_frontend_role({"report": report, "ordinary": ("node.exe", "fixed.mjs")})
