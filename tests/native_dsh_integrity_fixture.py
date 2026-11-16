"""Small independent dual-root identity for adversarial admission tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from genshin_app.agent_chat.native_dsh_dependency_payloads import (
    dependency_payload_inventory,
)
from genshin_app.agent_chat.runtime_integrity import (
    DSH_CLIENT_SHELL_RUNTIME_FILES,
    audit_runtime_closure,
)
from genshin_app.version import APP_VERSION, NATIVE_AGENT_NODE_VERSION


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def build_identity(tmp_path: Path):
    runtime, assets = tmp_path / "runtime", tmp_path / "assets"
    for name in DSH_CLIENT_SHELL_RUNTIME_FILES:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// independent integrity fixture: {name}\n", encoding="utf-8")
    (runtime / "package.json").write_bytes(canonical({"dependencies": {}}))
    (runtime / "node_modules").mkdir()
    (runtime / "node_modules/.modules.yaml").write_bytes(canonical({"nodeLinker": "hoisted"}))
    composition = {"dshRelease": "0.2.2-rc.3", "dshCommit": "fixture-commit",
                   "dynamicModules": [], "localModules": {}}
    accepted = {"dshRelease": "0.2.2-rc.3", "dshCommit": "fixture-commit", "entryOrder": []}
    unsigned = canonical(accepted)
    accepted["semanticSha256"] = sha(unsigned)
    files = {
        "dsh-client-composition.json": canonical(composition),
        "dsh-client-accepted-manifest.json": canonical(accepted),
        "package.json": canonical({"name": "independent-client-fixture"}),
        "pnpm-lock.yaml": b"fixture-lock\n",
        "dist/index.html": b"<!doctype html><main></main>",
        "dist/asset-manifest.json": b"{}\n",
    }
    for name, raw in files.items():
        target = assets / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    file_hashes = {name: sha(raw) for name, raw in sorted(files.items())}
    closure = audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=runtime)
    node = tmp_path / "node.exe"
    node.write_bytes(b"MZ\x00independent-non-executable-fixture")
    manifest = {
        "contract": "genshin-native-dsh-client-shell-manifest-v1", "phase": "dsh_client_shell",
        "app_version": APP_VERSION, "runtime_generation": "dsh_client_shell",
        "entry": "src/native-dsh-runtime-entry.mjs", "node_version": NATIVE_AGENT_NODE_VERSION,
        "dsh_release": "0.2.2-rc.3", "runtime_files": closure["files"],
        "runtime_closure_hash": closure["runtime_closure_hash"],
        "dependency_lock_hash": closure["dependency_lock_hash"], "node_binary_sha256": sha(node.read_bytes()),
        "dependency_cohort": {}, "dependency_payloads": dependency_payload_inventory(runtime),
        "asset_files": file_hashes,
        "asset_closure_hash": sha(canonical({
            "contract": "genshin-native-dsh-client-assets-closure-v1", "file_hashes": list(file_hashes.items()),
        })),
        "client_composition_hash": sha(files["dsh-client-composition.json"]),
        "client_accepted_hash": sha(files["dsh-client-accepted-manifest.json"]),
        "client_accepted_semantic_hash": accepted["semanticSha256"],
    }
    manifest["manifest_hash"] = sha(canonical(manifest))
    return SimpleNamespace(runtime=runtime, assets=assets, node=node, manifest=manifest)
