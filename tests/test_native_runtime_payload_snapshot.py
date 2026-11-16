"""Byte-complete admission remains independent of parallel I/O and cohort reuse."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import native_dsh_dependency_payloads as payloads
from genshin_app.agent_chat.runtime_integrity import (
    RuntimeIntegrityError,
    verify_runtime_integrity,
)
from tests.native_dsh_integrity_fixture import build_identity, canonical, sha
from tests.pinned_native_node import pinned_native_node
from tests.test_native_dsh_dependency_payloads import installed_tree, node_inventory

ROOT = Path(__file__).resolve().parents[1]


def test_parallel_inventory_keeps_all_batch_and_buffer_boundaries(tmp_path):
    runtime, modules, leaf = installed_tree(tmp_path)
    expected = {leaf.relative_to(modules).as_posix(): leaf.read_bytes()}
    for index in range(513):
        name = f"payloads/file-{index:04}.bin"
        raw = bytes([index % 251]) * (index % 131)
        target = modules / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
        expected[name] = raw
    for size in (65535, 65536, 65537, 131073):
        name = f"payloads/buffer-{size}.bin"
        raw = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        (modules / name).write_bytes(raw)
        expected[name] = raw
    metadata = json.loads((modules / ".modules.yaml").read_bytes())
    for field in ("storeDir", "virtualStoreDir", "prunedAt"):
        metadata.pop(field)
    expected[".modules.yaml"] = canonical(metadata) + b"\n"
    actual = payloads.dependency_payload_inventory(runtime)
    assert actual["files"] == {name: sha(raw) for name, raw in sorted(expected.items())}
    assert actual["total_bytes"] == sum(map(len, expected.values()))
    assert node_inventory(runtime) == actual


def test_late_worker_failure_never_returns_partial_inventory(tmp_path):
    runtime, modules, _ = installed_tree(tmp_path)
    for index in range(270):
        (modules / f"a-{index:04}.js").write_bytes(b"checked\n")
    bad = modules / "zz/.bin/bad"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"#!/bin/sh\n# cmd-shim-target=/somewhere\nexec unreviewed\n")
    with pytest.raises(payloads.DependencyPayloadError, match="dsh_dependency_shim_marker_invalid"):
        payloads.dependency_payload_inventory(runtime)
    assert node_inventory(runtime) == {"error": "dsh_dependency_shim_marker_invalid"}


def _make_cohort(tmp_path):
    value = build_identity(tmp_path)
    dependencies = {"@deepseek-ai/dsh-a": "0.2.2-rc.3", "@deepseek-ai/dsh-ab": "0.2.2-rc.3"}
    (value.runtime / "package.json").write_bytes(canonical({"dependencies": dependencies}))
    for name, version in dependencies.items():
        package = value.runtime / "node_modules" / name
        (package / ".bin").mkdir(parents=True)
        (package / "package.json").write_bytes(canonical({"name": name, "version": version}))
        (package / "index.js").write_bytes(f"export default '{name}'\n".encode())
        (package / "extra.json").write_bytes(b'{"notAllowlisted":true}')
        (package / ".bin/tool.CMD").write_bytes(b"@ECHO OFF\r\nnode ../index.js\r\n")
        (package / ".bin/tool").write_bytes(
            b'#!/bin/sh\nexec node "$basedir/../index.js"\n# cmd-shim-target=C:/build-only/index.js\n')
    node = pinned_native_node(required=False)
    if node is None:
        pytest.skip("Pinned Node fixture unavailable")
    script = """
import {buildDshClientShellManifest} from './agent-runtime/src/dsh-client-shell-runtime-contract.mjs';
const value=await buildDshClientShellManifest({runtimeRoot:process.argv[1],assetsRoot:process.argv[2],nodeExecutable:process.argv[3]});
console.log(JSON.stringify(value));
"""
    result = subprocess.run([str(node), "--input-type=module", "-e", script, str(value.runtime),
                             str(value.assets), str(value.node)], cwd=ROOT, check=True, capture_output=True, text=True)
    value.manifest = json.loads(result.stdout)
    return value


def _verify(value):
    return verify_runtime_integrity(value.manifest, phase="dsh_client_shell", runtime_root=value.runtime,
                                    assets_root=value.assets, node_executable=value.node)


def _resign_fixture(manifest):
    manifest.pop("manifest_hash")
    manifest["manifest_hash"] = sha(canonical(manifest))


def test_same_call_cohort_reuse_matches_independent_node_and_shim_projection(tmp_path):
    value = _make_cohort(tmp_path)
    actual = _verify(value)["dependency_cohort"]
    assert actual == value.manifest["dependency_cohort"]
    for name, row in actual.items():
        assert set(row["files"]) == {"package.json", "index.js", "extra.json", ".bin/tool", ".bin/tool.cmd"}
        assert row["files"]["index.js"] == sha(f"export default '{name}'\n".encode())
        assert row["files"][".bin/tool"] == sha(b'#!/bin/sh\nexec node "$basedir/../index.js"\n')


@pytest.mark.parametrize("drift", ["same_size_same_mtime", "extra", "missing"])
def test_reused_cohort_does_not_trust_previous_admission(tmp_path, drift):
    value = _make_cohort(tmp_path)
    _verify(value)
    package = value.runtime / "node_modules/@deepseek-ai/dsh-a"
    target = package / "index.js"
    if drift == "same_size_same_mtime":
        stamp = target.stat()
        target.write_bytes(target.read_bytes().replace(b"default", b"changed"))
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif drift == "extra":
        (package / "extra-execution.js").write_bytes(b"unadmitted\n")
    else:
        target.unlink()
    with pytest.raises(RuntimeIntegrityError, match="dsh_shell_dependency_payload_mismatch"):
        _verify(value)


def test_package_identity_drift_after_inventory_is_rejected(tmp_path, monkeypatch):
    value = _make_cohort(tmp_path)
    target = value.runtime / "node_modules/@deepseek-ai/dsh-a/package.json"
    inventory = payloads.dependency_payload_inventory

    def read_then_change(root):
        snapshot = inventory(root)
        target.write_bytes(target.read_bytes() + b"\n")
        return snapshot

    monkeypatch.setattr(payloads, "dependency_payload_inventory", read_then_change)
    with pytest.raises(RuntimeIntegrityError, match="dsh_shell_dependency_identity"):
        _verify(value)


def test_internal_alias_is_not_hidden_by_cohort_snapshot_reuse(tmp_path):
    value = _make_cohort(tmp_path)
    package = value.runtime / "node_modules/@deepseek-ai/dsh-a"
    try:
        (package / "alias").symlink_to(package / "extra.json")
    except OSError:
        pytest.skip("Creating a symlink needs the Windows developer privilege")
    # Even an inventory that declares this in-root alias cannot waive the
    # stricter DSH package rule. The runtime must reject before cohort matching.
    value.manifest["dependency_payloads"] = payloads.dependency_payload_inventory(value.runtime)
    _resign_fixture(value.manifest)
    with pytest.raises(RuntimeIntegrityError, match="dsh_shell_dependency_alias"):
        _verify(value)


def test_worker_io_error_is_not_replaced_with_manifest_hash(tmp_path, monkeypatch):
    value = _make_cohort(tmp_path)
    target = value.runtime / "node_modules/@deepseek-ai/dsh-ab/index.js"
    original = Path.open

    def deny_one_file(path, *args, **kwargs):
        if path == target:
            raise PermissionError("test inaccessible payload")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_one_file)
    with pytest.raises(RuntimeIntegrityError, match="dsh_shell_dependency_payload_invalid"):
        _verify(value)
