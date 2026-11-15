"""Actual payload identity and the narrow pnpm publication projection."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_dsh_dependency_payloads import (
    DependencyPayloadError,
    dependency_payload_inventory,
)
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]


def installed_tree(tmp_path):
    runtime = tmp_path / "runtime"
    modules = runtime / "node_modules"
    leaf = modules / ".pnpm/dependency@1/node_modules/dependency/index.js"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"export default 'real transitive bytes'\n")
    (modules / ".modules.yaml").write_text(json.dumps({
        "storeDir": "C:/machine/private-store", "virtualStoreDir": "C:/machine/.pnpm",
        "prunedAt": "2026-11-05T01:00:00Z", "included": {"production": True},
        "note": "中文元数据",
    }), encoding="utf-8")
    (modules / ".pnpm-workspace-state-v1.json").write_text('{"installCache":1}', encoding="utf-8")
    return runtime, modules, leaf


def node_inventory(runtime):
    node = pinned_native_node(required=False)
    if node is None:
        pytest.skip("Pinned Node fixture unavailable")
    script = """
import {dependencyPayloadInventory} from './agent-runtime/src/dependency-payloads.mjs';
try { console.log(JSON.stringify(await dependencyPayloadInventory(process.argv[1]))); }
catch(error) { console.log(JSON.stringify({error:error.code})); }
"""
    result = subprocess.run([str(node), "--input-type=module", "-e", script, str(runtime)],
                            check=True, capture_output=True, text=True, cwd=ROOT)
    return json.loads(result.stdout)


def test_full_payload_node_python_and_portable_metadata_agree(tmp_path):
    runtime, modules, leaf = installed_tree(tmp_path)
    value = dependency_payload_inventory(runtime)
    assert node_inventory(runtime) == value
    assert len(value["files"]) == 2
    assert value["files"][leaf.relative_to(modules).as_posix()] == hashlib.sha256(leaf.read_bytes()).hexdigest()
    metadata = json.loads((modules / ".modules.yaml").read_text(encoding="utf-8"))
    metadata.pop("storeDir")
    metadata.pop("virtualStoreDir")
    metadata.pop("prunedAt")
    normalized = (json.dumps(metadata, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert value["files"][".modules.yaml"] == hashlib.sha256(normalized).hexdigest()
    assert value["total_bytes"] == len(normalized) + leaf.stat().st_size
    (modules / ".modules.yaml").write_bytes(normalized)
    (modules / ".pnpm-workspace-state-v1.json").unlink()
    assert dependency_payload_inventory(runtime) == value
    assert node_inventory(runtime) == value


@pytest.mark.parametrize("drift", ["tamper", "missing", "extra", "metadata"])
def test_transitive_and_non_allowlisted_metadata_drift_change_identity(tmp_path, drift):
    runtime, modules, leaf = installed_tree(tmp_path)
    before = dependency_payload_inventory(runtime)
    if drift == "tamper":
        leaf.write_bytes(leaf.read_bytes() + b"modified")
    elif drift == "missing":
        leaf.unlink()
    elif drift == "extra":
        (modules / "unexpected-production.js").write_bytes(b"unlisted code")
    else:
        path = modules / ".modules.yaml"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["included"] = {"production": False}
        path.write_text(json.dumps(metadata), encoding="utf-8")
    after = dependency_payload_inventory(runtime)
    assert after != before
    assert node_inventory(runtime) == after


def test_generated_shim_metadata_and_cmd_case_project_without_hiding_code(tmp_path):
    runtime, modules, _leaf = installed_tree(tmp_path)
    shim_dir = modules / ".bin"
    shim_dir.mkdir()
    shim = shim_dir / "dependency"
    wrapper = b'#!/bin/sh\nexec node "$basedir/../dependency/index.js" "$@"\n'
    marker = b"# cmd-shim-target="
    shim.write_bytes(wrapper + marker + b"C:/fixture-one/dependency/index.js\n")
    command = shim_dir / "dependency.CMD"
    command.write_bytes(b"@ECHO OFF\r\nnode ..\\dependency\\index.js %*\r\n")
    before = dependency_payload_inventory(runtime)
    assert node_inventory(runtime) == before
    assert before["files"][".bin/dependency"] == hashlib.sha256(wrapper).hexdigest()
    assert before["files"][".bin/dependency.cmd"] == hashlib.sha256(command.read_bytes()).hexdigest()
    assert ".bin/dependency.CMD" not in before["files"]

    metadata_path = modules / ".modules.yaml"
    metadata = json.loads(metadata_path.read_bytes())
    metadata["prunedAt"] = "2026-11-07T01:00:00Z"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    shim.write_bytes(wrapper + marker + b"/fixture-two/dependency/index.js\n")
    assert dependency_payload_inventory(runtime) == before
    assert node_inventory(runtime) == before

    shim.write_bytes(wrapper.replace(b"index.js", b"other.js") + marker + b"/fixture-two/dependency/index.js\n")
    changed = dependency_payload_inventory(runtime)
    assert changed != before
    assert node_inventory(runtime) == changed

    shim.write_bytes(wrapper + marker + b"/fixture-two/dependency/index.js\nexec something\n")
    with pytest.raises(DependencyPayloadError, match="dsh_dependency_shim_marker_invalid"):
        dependency_payload_inventory(runtime)
    assert node_inventory(runtime) == {"error": "dsh_dependency_shim_marker_invalid"}


def test_alias_must_resolve_into_inventoried_tree(tmp_path):
    runtime, modules, leaf = installed_tree(tmp_path)
    inside = modules / "dependency"
    try:
        inside.symlink_to(leaf.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a symlink needs the Windows developer privilege")
    value = dependency_payload_inventory(runtime)
    assert value["aliases"] == {"dependency": leaf.parent.relative_to(modules).as_posix()}
    assert node_inventory(runtime) == value
    inside.unlink()
    outside = tmp_path / "external"
    outside.mkdir()
    inside.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DependencyPayloadError, match="dsh_dependency_alias_escape"):
        dependency_payload_inventory(runtime)
    assert node_inventory(runtime) == {"error": "dsh_dependency_alias_escape"}
