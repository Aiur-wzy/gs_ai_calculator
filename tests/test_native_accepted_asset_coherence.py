"""The accepted release identity must match today's actual frontend artifacts.

This is an artifact-consistency check, not a replacement production verifier.
It deliberately never runs the generator, changes a pin, or substitutes a new
candidate oracle. Full dependency/runtime admission remains a release preflight.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from genshin_app.agent_chat.runtime_integrity import DSH_CLIENT_SHELL_RUNTIME_FILES
from genshin_app.version import NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "agent-runtime/dsh-client-shell-runtime-manifest.json"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _accepted_manifest(path=MANIFEST):
    raw = path.read_bytes()
    assert _sha(raw) == NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256, "accepted_release_pin_mismatch"
    value = json.loads(raw)
    body = {key: item for key, item in value.items() if key != "manifest_hash"}
    assert _sha(_canonical(body)) == value["manifest_hash"], "accepted_manifest_canonical_hash_mismatch"
    return value


def _assert_asset_coherence(assets, manifest):
    composition = json.loads((assets / "dsh-client-composition.json").read_text("utf-8"))
    names = {"dsh-client-composition.json", "dsh-client-accepted-manifest.json",
             "package.json", "pnpm-lock.yaml"}
    assert (assets / "dist").is_dir(), "accepted_asset_dist_missing"
    names.update(path.relative_to(assets).as_posix()
                 for path in (assets / "dist").rglob("*") if path.is_file())
    for local in composition.get("localModules", {}).values():
        host = Path(local["hostEntry"])
        names.update({host.as_posix(), (host.parent / "package.json").as_posix()})
        names.update(path.relative_to(assets).as_posix()
                     for path in (assets / host.parent / "lib").rglob("*") if path.is_file())
    declared = manifest["asset_files"]
    assert names == set(declared), {
        "code": "accepted_asset_file_set_mismatch", "added": sorted(names - set(declared)),
        "missing": sorted(set(declared) - names),
    }
    actual = {name: _sha((assets / name).read_bytes()) for name in sorted(names)}
    changed = {name: {"expected": declared[name], "actual": actual[name]}
               for name in actual if actual[name] != declared[name]}
    assert not changed, {"code": "accepted_asset_bytes_mismatch", "changed": changed}
    assert _sha(_canonical({"contract": "genshin-native-dsh-client-assets-closure-v1",
                            "file_hashes": list(actual.items())})) == manifest["asset_closure_hash"]


def _assert_runtime_coherence(runtime, manifest):
    declared = manifest["runtime_files"]
    assert set(declared) == set(DSH_CLIENT_SHELL_RUNTIME_FILES), "accepted_runtime_declared_file_set_mismatch"
    missing = [name for name in declared if not (runtime / name).is_file()]
    assert not missing, {"code": "accepted_runtime_file_missing", "missing": missing}
    actual = {name: _sha((runtime / name).read_bytes()) for name in sorted(declared)}
    changed = {name: {"expected": declared[name], "actual": actual[name]}
               for name in actual if actual[name] != declared[name]}
    assert not changed, {"code": "accepted_runtime_bytes_mismatch", "changed": changed}
    assert _sha(_canonical({"contract": "genshin-native-runtime-closure-v1", "phase": "dsh_client_shell",
                            "file_hashes": list(actual.items())})) == manifest["runtime_closure_hash"]
    assert actual["pnpm-lock.yaml"] == manifest["dependency_lock_hash"]


def test_accepted_release_identity_matches_current_complete_asset_tree():
    _assert_asset_coherence(ROOT / "frontend", _accepted_manifest())


def test_accepted_release_identity_matches_current_declared_runtime_source_tree():
    _assert_runtime_coherence(ROOT / "agent-runtime", _accepted_manifest())


@pytest.mark.parametrize("mutation", ["bytes", "missing"])
def test_accepted_runtime_copy_rejects_source_drift_without_new_oracle(tmp_path, mutation):
    manifest = _accepted_manifest()
    runtime = tmp_path / "runtime"
    for name in manifest["runtime_files"]:
        destination = runtime / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "agent-runtime" / name, destination)
    _assert_runtime_coherence(runtime, manifest)
    path = runtime / manifest["entry"]
    if mutation == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n// independent runtime copy drift\n")
    with pytest.raises(AssertionError, match="accepted_runtime_"):
        _assert_runtime_coherence(runtime, manifest)


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra", "local_host"])
def test_accepted_asset_copy_rejects_drift_without_regenerating_its_oracle(tmp_path, mutation):
    manifest = _accepted_manifest()
    assets = tmp_path / "assets"
    for name in manifest["asset_files"]:
        destination = assets / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "frontend" / name, destination)
    # A pre-existing stale source must fail here, rather than making the
    # deliberately corrupted-copy assertion appear successful by accident.
    _assert_asset_coherence(assets, manifest)
    selected = next(name for name in sorted(manifest["asset_files"])
                    if name.startswith("dist/") and name.endswith(".js"))
    path = assets / selected
    if mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        (assets / "dist/unreviewed-copy-only.js").write_bytes(b"// independent extra asset\n")
    else:
        if mutation == "local_host":
            composition = json.loads((assets / "dsh-client-composition.json").read_text("utf-8"))
            path = assets / next(iter(composition["localModules"].values()))["hostEntry"]
        path.write_bytes(path.read_bytes() + b"\n// independent copy drift\n")
    with pytest.raises(AssertionError, match="accepted_asset_"):
        _assert_asset_coherence(assets, manifest)


def test_accepted_manifest_copy_cannot_repin_its_own_modified_bytes(tmp_path):
    path = tmp_path / MANIFEST.name
    path.write_bytes(MANIFEST.read_bytes() + b"\n")
    with pytest.raises(AssertionError, match="accepted_release_pin_mismatch"):
        _accepted_manifest(path)
