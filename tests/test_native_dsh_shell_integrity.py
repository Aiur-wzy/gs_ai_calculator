"""Independent new-shell identity for source and trimmed release layouts."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.runtime_integrity import (
    DSH_CLIENT_SHELL_RUNTIME_FILES,
    RuntimeIntegrityError,
    verify_runtime_integrity,
)
from scripts.native_agent_release import _materialize_dependency_release_metadata
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.release_inventory


@pytest.fixture(scope="module")
def shell_identity(tmp_path_factory):
    node = pinned_native_node(required=False)
    if node is None:
        pytest.skip("Pinned Node fixture unavailable")
    scratch = tmp_path_factory.mktemp("new-shell-integrity")
    candidate = scratch / "candidate.json"
    subprocess.run([str(node), str(ROOT / "scripts/generate_dsh_client_shell_manifest.mjs"),
                    str(ROOT / "agent-runtime"), str(ROOT / "frontend"), str(node), str(candidate)],
                   check=True, capture_output=True, text=True, cwd=ROOT)
    manifest = json.loads(candidate.read_text())
    runtime = scratch / "runtime"
    assets = scratch / "assets"

    def copy_file(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    for name in manifest["runtime_files"]:
        copy_file(ROOT / "agent-runtime" / name, runtime / name)
    modules = runtime / "node_modules"
    shutil.copytree(ROOT / "agent-runtime/node_modules", modules, symlinks=True)
    _materialize_dependency_release_metadata(modules)
    for name in manifest["asset_files"]:
        copy_file(ROOT / "frontend" / name, assets / name)
    return manifest, runtime, assets, node


def verify(manifest, runtime, assets, node):
    return verify_runtime_integrity(manifest, phase="dsh_client_shell", runtime_root=runtime,
                                    assets_root=assets, node_executable=node)


def test_generator_and_python_verifier_agree_for_trimmed_portable_shape(shell_identity):
    manifest, runtime, assets, node = shell_identity
    assert tuple(manifest["runtime_files"]) == DSH_CLIENT_SHELL_RUNTIME_FILES
    assert not any(any(token in name for token in ("probe", "fixture", "fake", "qualification"))
                   for name in manifest["runtime_files"])
    assert len(manifest["asset_files"]) > 10
    assert sum(len(row["files"]) for row in manifest["dependency_cohort"].values()) > 100
    assert len(manifest["dependency_payloads"]["files"]) > sum(
        len(row["files"]) for row in manifest["dependency_cohort"].values())
    value = verify(manifest, runtime, assets, node)
    assert value["closure"]["runtime_closure_hash"] == manifest["runtime_closure_hash"]
    assert value["assets"]["asset_closure_hash"] == manifest["asset_closure_hash"]
    # The Node verifier reads the independent release roots, rather than its
    # own source directory or the original development asset installation.
    source = """
import {verifyDshClientShellManifest} from './agent-runtime/src/dsh-client-shell-runtime-contract.mjs';
import {readFile} from 'node:fs/promises';
const manifest=JSON.parse(await readFile(process.argv[1],'utf8'));
const value=await verifyDshClientShellManifest(manifest,{
 runtimeRoot:process.argv[2],assetsRoot:process.argv[3],nodeExecutable:process.execPath});
console.log(JSON.stringify(value));
"""
    path = runtime.parent / "trimmed-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run([str(node), "--input-type=module", "-e", source, str(path),
                             str(runtime), str(assets)], check=True, capture_output=True, text=True, cwd=ROOT)
    assert json.loads(result.stdout)["valid"] is True


@pytest.mark.parametrize("target", ["runtime", "asset", "dependency", "transitive", "extra_dependency", "missing_asset", "manifest"])
def test_closed_identity_rejects_drift_and_missing_payloads(shell_identity, target):
    manifest, runtime, assets, node = shell_identity
    if target == "manifest":
        altered = {**manifest, "entry": "src/native-sidecar-entry.mjs"}
        with pytest.raises(RuntimeIntegrityError, match="dsh_shell_manifest_hash"):
            verify(altered, runtime, assets, node)
        return
    if target == "runtime":
        path = runtime / manifest["entry"]
    elif target == "transitive":
        name = next(name for name in manifest["dependency_payloads"]["files"]
                    if not name.startswith("@deepseek-ai/") and name.endswith(".js"))
        path = runtime / "node_modules" / name
    elif target == "extra_dependency":
        path = runtime / "node_modules" / "unreviewed-runtime-code.js"
    elif target in {"asset", "missing_asset"}:
        name = next(name for name in manifest["asset_files"] if name.endswith(".js") and name.startswith("dist/"))
        path = assets / name
    else:
        package, row = next((name, row) for name, row in manifest["dependency_cohort"].items()
                            if any(file.endswith(".js") for file in row["files"]))
        name = next(file for file in row["files"] if file.endswith(".js"))
        path = runtime / "node_modules" / package / name
    original = path.read_bytes() if path.exists() else None
    try:
        if target == "missing_asset":
            path.unlink()
        else:
            path.write_bytes((original or b"") + b"\n/* isolated drift fixture */\n")
        with pytest.raises(RuntimeIntegrityError):
            verify(manifest, runtime, assets, node)
    finally:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
