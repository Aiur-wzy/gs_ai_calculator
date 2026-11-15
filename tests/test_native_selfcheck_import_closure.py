"""Both release verifiers enumerate the guarded selfcheck's exact local loads."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.runtime_integrity import (
    DSH_CLIENT_SHELL_RUNTIME_FILES,
    RuntimeIntegrityError,
    audit_runtime_closure,
)
from tests.native_dsh_integrity_fixture import build_identity
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "src/native-dsh-selfcheck-entry.mjs"
CASES = [
    ("await import('./native-dsh-selfcheck-provider.mjs')", None),
    ("await import('./native-mux-bootstrap.mjs')", None),
    ("await import('./native-dsh-session-host.mjs')", None),
    ("await import('./native-dsh-mux-gateway.mjs')", None),
    ("await import('./native-dsh-domain-transport.mjs')", None),
    ("await import( '././native-dsh-runtime-config.mjs' )", None),
    ("await import('./native-app-system-failure.mjs')", "runtime_integrity_unlisted_import"),
    ("await import('./new-unreviewed.mjs')", "runtime_integrity_unlisted_import"),
    ("await import('../../escape.mjs')", "runtime_integrity_import_escape"),
    ("await import(name)", "runtime_integrity_dynamic_import"),
    ("await import(`./native-dsh-session-host.mjs`)", "runtime_integrity_dynamic_import"),
    ("await import('./native-' + name)", "runtime_integrity_dynamic_import"),
    ("await import('./native-dsh-session-host.mjs?query')", "runtime_integrity_dynamic_import"),
    ("await import('./native-dsh-session-host.mjs#hash')", "runtime_integrity_dynamic_import"),
    ("await import /*comment*/ ('./native-dsh-session-host.mjs')", "runtime_integrity_dynamic_import"),
    ("await import //comment\n ('./native-dsh-session-host.mjs')", "runtime_integrity_dynamic_import"),
    ("import('./native-dsh-session-host.mjs')", "runtime_integrity_dynamic_import"),
]


def node_audit(runtime):
    node = pinned_native_node()
    script = f"""import {{auditRuntimeClosure,DSH_CLIENT_SHELL_RUNTIME_FILES}} from {json.dumps((ROOT/'agent-runtime/src/runtime-integrity.mjs').as_uri())};
try {{let r=await auditRuntimeClosure({{phase:'dsh_client_shell',files:DSH_CLIENT_SHELL_RUNTIME_FILES,root:process.argv[1]}});console.log(JSON.stringify({{ok:true,files:Object.keys(r.files)}}));}}
catch(e){{console.log(JSON.stringify({{ok:false,code:e.code}}));}}"""
    result = subprocess.run([str(node), "--input-type=module", "-e", script, str(runtime)],
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


@pytest.mark.parametrize("source,error", CASES)
def test_exact_deferred_loader_contract_matches_in_python_and_actual_node(tmp_path, source, error):
    value = build_identity(tmp_path)
    (value.runtime / ENTRY).write_text(source, encoding="utf-8")
    if error is None:
        result = audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=value.runtime)
        assert tuple(result["files"]) == DSH_CLIENT_SHELL_RUNTIME_FILES
    else:
        with pytest.raises(RuntimeIntegrityError) as caught:
            audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=value.runtime)
        assert caught.value.code == error
    actual = node_audit(value.runtime)
    assert actual["ok"] is (error is None)
    if error:
        assert actual["code"] == error


def test_real_production_closure_recursively_admits_all_imports_in_both_verifiers():
    result = audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=ROOT / "agent-runtime")
    assert tuple(result["files"]) == DSH_CLIENT_SHELL_RUNTIME_FILES
    assert "src/native-app-system-failure.mjs" in result["files"]
    assert not any("probe" in name or "fixture" in name for name in result["files"])
    actual = node_audit(ROOT / "agent-runtime")
    assert actual["ok"] is True
    assert actual["files"] == list(DSH_CLIENT_SHELL_RUNTIME_FILES)


def test_deferred_loader_is_still_forbidden_in_normal_production_entry(tmp_path):
    value = build_identity(tmp_path)
    (value.runtime / "src/native-dsh-runtime.mjs").write_text("await import('./native-dsh-session-host.mjs')", encoding="utf-8")
    with pytest.raises(RuntimeIntegrityError, match="runtime_integrity_dynamic_import"):
        audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=value.runtime)
    assert node_audit(value.runtime)["code"] == "runtime_integrity_dynamic_import"
