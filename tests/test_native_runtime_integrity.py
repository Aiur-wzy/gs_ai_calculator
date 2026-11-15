"""Current-shell integrity checks retain cross-root and adversarial coverage."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import runtime_integrity as integrity
from genshin_app.agent_chat.runtime_integrity import (
    DSH_CLIENT_SHELL_RUNTIME_FILES,
    RuntimeIntegrityError,
    audit_node_executable,
    audit_runtime_closure,
    verify_runtime_integrity,
)
from tests.native_dsh_integrity_fixture import build_identity
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]


def verify(value):
    return verify_runtime_integrity(value.manifest, phase="dsh_client_shell", runtime_root=value.runtime,
                                    assets_root=value.assets, node_executable=value.node)


def test_current_dual_root_manifest_and_node_are_independently_verifiable(tmp_path):
    value = build_identity(tmp_path)
    result = verify(value)
    assert result["closure"]["files"] == value.manifest["runtime_files"]
    assert result["assets"]["files"] == value.manifest["asset_files"]
    assert result["node"] == audit_node_executable(value.node)
    assert not any(name.startswith("PHASE") for name in vars(integrity))


@pytest.mark.parametrize("phase", ["phase1", "phase2_fake", "phase3_read", "phase3_sequential_read",
                                  "phase4_candidate_write", "phase5_secret_model", "phase5b_official_turn"])
def test_retired_phases_are_rejected_before_reading_any_runtime(phase, tmp_path):
    for call in (
        lambda: audit_runtime_closure(phase=phase, files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=tmp_path / "missing"),
        lambda: verify_runtime_integrity({}, phase=phase, runtime_root=tmp_path / "missing", node_executable=tmp_path / "missing-node"),
    ):
        with pytest.raises(RuntimeIntegrityError) as caught:
            call()
        assert caught.value.code == "runtime_integrity_phase"


@pytest.mark.parametrize("target,expected", [
    ("runtime/src/native-dsh-runtime.mjs", "dsh_shell_runtime_evidence_mismatch"),
    ("runtime/pnpm-lock.yaml", "dsh_shell_runtime_evidence_mismatch"),
    ("node.exe", "dsh_shell_runtime_evidence_mismatch"),
    ("assets/dist/index.html", "dsh_shell_asset_evidence_mismatch"),
    ("runtime/node_modules/unreviewed-payload.js", "dsh_shell_dependency_payload_mismatch"),
])
def test_current_admission_rejects_source_lock_node_assets_and_payload_tampering(tmp_path, target, expected):
    value = build_identity(tmp_path)
    path = tmp_path / target
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeIntegrityError) as caught:
        verify(value)
    assert caught.value.code == expected


@pytest.mark.parametrize("name,expected", [
    ("native-shadow-sidecar-entry.mjs", "runtime_integrity_unknown_sidecar_entry"),
    ("phase-old-runtime-contract.mjs", "runtime_integrity_unknown_contract_entry"),
])
def test_retired_or_unreviewed_executable_entries_cannot_join_current_closure(tmp_path, name, expected):
    value = build_identity(tmp_path)
    (value.runtime / "src" / name).write_text("// unadmitted", encoding="utf-8")
    with pytest.raises(RuntimeIntegrityError) as caught:
        audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=value.runtime)
    assert caught.value.code == expected


def test_missing_source_is_rejected_without_substituting_an_old_phase(tmp_path):
    value = build_identity(tmp_path)
    (value.runtime / "src/native-dsh-runtime.mjs").unlink()
    with pytest.raises(RuntimeIntegrityError) as caught:
        verify(value)
    assert caught.value.code == "runtime_integrity_file_type"


@pytest.mark.parametrize("source,expected", [
    ("import './unlisted-runtime-helper.mjs'", "runtime_integrity_unlisted_import"),
    ("new URL('./unlisted-runtime-helper.mjs', import.meta.url).href", "runtime_integrity_unlisted_import"),
    ("const load = createRequire(import.meta.url)", "runtime_integrity_ambient_loader"),
    ("require('./unlisted-runtime-helper.mjs')", "runtime_integrity_ambient_loader"),
    ("module.require('./unlisted-runtime-helper.mjs')", "runtime_integrity_ambient_loader"),
    ("process.getBuiltinModule('fs')", "runtime_integrity_ambient_loader"),
    ("process['getBuiltinModule']('fs')", "runtime_integrity_ambient_loader"),
    ("getBuiltinModule('fs')", "runtime_integrity_ambient_loader"),
    ("await import('./unlisted-runtime-helper.mjs')", "runtime_integrity_dynamic_import"),
])
def test_current_closure_rejects_unlisted_and_ambient_module_loaders(tmp_path, source, expected):
    value = build_identity(tmp_path)
    (value.runtime / "src/native-dsh-runtime.mjs").write_text(source, encoding="utf-8")
    with pytest.raises(RuntimeIntegrityError) as caught:
        verify(value)
    assert caught.value.code == expected


def test_node_closure_rejects_unlisted_profile_loader_module_url(tmp_path):
    selected_node = pinned_native_node(required=False)
    if selected_node is None:
        pytest.skip("pinned Node fixture unavailable")
    value = build_identity(tmp_path)
    (value.runtime / "src/native-dsh-runtime.mjs").write_text(
        "new URL('./unlisted-profile-plugin.mjs', import.meta.url).href", encoding="utf-8")
    url = (ROOT / "agent-runtime/src/runtime-integrity.mjs").as_uri()
    script = f"""import {{auditRuntimeClosure,DSH_CLIENT_SHELL_RUNTIME_FILES}} from {json.dumps(url)};
try {{ await auditRuntimeClosure({{phase:'dsh_client_shell',files:DSH_CLIENT_SHELL_RUNTIME_FILES,root:process.argv[1]}}); }}
catch(error) {{ process.stdout.write(error.code); }}"""
    result = subprocess.run([str(selected_node), "--input-type=module", "-e", script, str(value.runtime)],
                            check=True, capture_output=True, text=True, timeout=30)
    assert result.stdout == "runtime_integrity_unlisted_import"


def test_python_and_current_node_share_the_exact_execution_closure(tmp_path):
    selected_node = pinned_native_node(required=False)
    if selected_node is None:
        pytest.skip("pinned Node fixture unavailable")
    value = build_identity(tmp_path)
    url = (ROOT / "agent-runtime/src/runtime-integrity.mjs").as_uri()
    script = f"""import {{auditRuntimeClosure,DSH_CLIENT_SHELL_RUNTIME_FILES}} from {json.dumps(url)};
const result=await auditRuntimeClosure({{phase:'dsh_client_shell', files:DSH_CLIENT_SHELL_RUNTIME_FILES,root:process.argv[1]}});
process.stdout.write(JSON.stringify(result));"""
    result = subprocess.run([str(selected_node), "--input-type=module", "-e", script, str(value.runtime)],
                            check=True, capture_output=True, text=True, timeout=30)
    node = json.loads(result.stdout)
    node["dependency_lock_hash"] = node.pop("dependencyLockHash")
    node["runtime_closure_hash"] = node.pop("runtimeClosureHash")
    assert node == audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=value.runtime)
