"""Hermetic dual-root staging contracts; no EXE, GUI or Agent is launched here."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.version import APP_VERSION
from scripts import native_agent_release as release
from tests.test_native_agent_release_packaging import _make_dependency_metadata_fixture


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def shell_stage_fixture(tmp_path, monkeypatch):
    source = tmp_path / "source"
    runtime, assets = source / release.RUNTIME_DIRECTORY, source / "frontend"
    _make_dependency_metadata_fixture(runtime)
    payloads = {
        runtime / "src/native-dsh-runtime-entry.mjs": b"// fixture, never executed\n",
        runtime / "pnpm-lock.yaml": b"fixture-lock\n",
        runtime
        / "node_modules/transitive-package/deep/payload.js": b"transitive payload\n",
        assets / "dist/index.html": b"<!doctype html><div id='root'></div>",
        assets / "packages/app-domain/host.mjs": b"export function apply() {}\n",
        assets / "packages/app-domain/lib/client.js": b"// fixture client\n",
    }
    for path, content in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (assets / "unreviewed-debug.txt").write_text("must not be staged", encoding="utf8")
    node = source / "node.exe"
    node.write_bytes(b"fixture-node-not-executable")
    manifest = {
        "app_version": APP_VERSION,
        "phase": "dsh_client_shell",
        "runtime_generation": "dsh_client_shell",
        "node_version": release.NATIVE_AGENT_NODE_VERSION,
        "dsh_release": release.NATIVE_AGENT_DSH_RELEASE.removeprefix("dsh-v"),
        "node_binary_sha256": _sha(node),
        "runtime_files": {
            name: _sha(runtime / name)
            for name in (
                "src/native-dsh-runtime-entry.mjs",
                "package.json",
                "pnpm-lock.yaml",
            )
        },
        "runtime_closure_hash": "a" * 64,
        "dependency_lock_hash": _sha(runtime / "pnpm-lock.yaml"),
        "dependency_cohort": {},
        "asset_files": {
            path.relative_to(assets).as_posix(): _sha(path)
            for path in payloads
            if assets in path.parents
        },
        "asset_closure_hash": "b" * 64,
    }
    manifest_path = runtime / release.RUNTIME_MANIFEST
    manifest_path.write_text(json.dumps(manifest), encoding="utf8")
    monkeypatch.setattr(
        release, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", _sha(manifest_path)
    )
    monkeypatch.setattr(release, "NATIVE_AGENT_NODE_BINARY_SHA256", _sha(node))
    monkeypatch.setattr(
        release,
        "_read_node_version",
        lambda *_a, **_kw: release.NATIVE_AGENT_NODE_VERSION,
    )
    monkeypatch.setattr(
        release,
        "_run_dependency_runtime_gate",
        lambda *_a, **_kw: {"fixture_import_gate": True},
    )
    calls = []

    def public_verifier(**options):
        # Substitute only the cryptographic/runtime-module verifier. All staging,
        # complete transitive-tree validation and root selection remain real.
        calls.append(options)
        assert options["verification_mode"] == "release"
        assert _sha(options["manifest_path"]) == options["expected_manifest_sha256"]
        for table, root in (
            ("runtime_files", Path(options["runtime_root"])),
            ("asset_files", Path(options["assets_root"])),
        ):
            for name, digest in manifest[table].items():
                path = root / name
                if not path.is_file() or _sha(path) != digest:
                    raise NativeDshMuxError("fixture_declared_payload_changed")
        return {"manifest": copy.deepcopy(manifest)}

    monkeypatch.setattr(release, "verify_native_dsh_production", public_verifier)
    plan = release.create_native_agent_build_plan(project_root=source, native_node=node)
    allowed = tmp_path / "build"
    allowed.mkdir()
    return plan, allowed, calls


def test_staging_and_package_verify_both_roots_and_keep_full_transitive_payload(
    shell_stage_fixture,
):
    plan, allowed, calls = shell_stage_fixture
    source_metadata = (plan.runtime_root / "node_modules/.modules.yaml").read_bytes()
    stage = release.prepare_native_agent_stage(
        plan, staging_root=allowed / "stage", allowed_parent=allowed
    )
    assert plan.public()["runtime_generation"] == "dsh_client_shell"
    assert calls[0]["assets_root"] == plan.assets_root
    assert calls[1]["assets_root"] == stage / release.ASSETS_DIRECTORY
    assert (
        stage / "agent-runtime/node_modules/transitive-package/deep/payload.js"
    ).read_bytes() == b"transitive payload\n"
    assert not (stage / "agent-client-assets/unreviewed-debug.txt").exists()
    assert (
        plan.runtime_root / "node_modules/.modules.yaml"
    ).read_bytes() == source_metadata
    staged = release.validate_staged_native_agent_runtime(stage)
    assert staged["agent_started"] is False
    package = allowed / "GenshinCalc"
    shutil.copytree(stage, package / "_internal")
    checked = release.validate_packaged_native_agent_runtime(plan, package_root=package)
    assert checked["project_asset_files"] == len(plan.manifest["asset_files"])
    assert calls[-1]["assets_root"] == package / "_internal/agent-client-assets"


@pytest.mark.parametrize("target", ["asset", "transitive"])
def test_packaged_root_drift_is_rejected_without_an_old_runtime_fallback(
    shell_stage_fixture, target
):
    plan, allowed, _calls = shell_stage_fixture
    stage = release.prepare_native_agent_stage(
        plan, staging_root=allowed / "stage", allowed_parent=allowed
    )
    package = allowed / "GenshinCalc"
    shutil.copytree(stage, package / "_internal")
    path = (
        package / "_internal/agent-client-assets/dist/index.html"
        if target == "asset"
        else package
        / "_internal/agent-runtime/node_modules/transitive-package/deep/payload.js"
    )
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.validate_packaged_native_agent_runtime(plan, package_root=package)
    assert caught.value.code == (
        "native_release_package_runtime_verification_failed"
        if target == "asset"
        else "native_release_package_dependency_layout_mismatch"
    )


def test_an_unaccepted_new_manifest_never_reuses_the_old_phase_pin(
    shell_stage_fixture, monkeypatch
):
    plan, _allowed, _calls = shell_stage_fixture
    monkeypatch.setattr(release, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", None)
    monkeypatch.setattr(
        release,
        "_read_node_version",
        lambda *_a, **_kw: pytest.fail("pin admission must be first"),
    )
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.create_native_agent_build_plan(
            project_root=plan.runtime_root.parent, native_node=plan.node_executable
        )
    assert caught.value.code == "native_release_shell_manifest_pin_missing"


def test_cohort_compares_exact_identities_rather_than_accepting_a_matching_count(
    tmp_path,
):
    runtime = tmp_path / "runtime"
    _make_dependency_metadata_fixture(runtime)
    package = runtime / "package.json"
    package.write_text(
        json.dumps({"dependencies": {"@deepseek-ai/fixture": "1.2.3"}}), encoding="utf8"
    )
    installed = runtime / "node_modules/@deepseek-ai/fixture"
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(
        json.dumps({"name": "@deepseek-ai/fixture", "version": "1.2.3"}),
        encoding="utf8",
    )
    expected = {"@deepseek-ai/fixture": {"version": "1.2.3"}}
    release._validate_dependency_closure(runtime, expected_cohort=expected)
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(
            runtime, expected_cohort={"@deepseek-ai/other": {"version": "1.2.3"}}
        )
    assert caught.value.code == "native_release_dependency_cohort_mismatch"


def test_spec_uses_public_database_validator_and_shared_dual_root_stage_gate():
    spec = Path(__file__).parents[1] / "genshin_app.spec"
    text = spec.read_text("utf8")
    assert "validate_database_release_manifest(json.loads" in text
    assert (
        'database_manifest.get("contract") != "genshin-database-release-manifest-v2"'
        not in text
    )
    assert "validate_staged_native_agent_runtime(native_stage)" in text
    assert '(str(native_assets), "agent-client-assets")' in text
    assert "load_phase5b" not in text


@pytest.mark.parametrize("table", ["runtime_files", "asset_files"])
@pytest.mark.parametrize(
    "name", ["../escape.txt", "/absolute.txt", "C:/escape.txt", "bad\\path.txt"]
)
def test_untrusted_file_maps_cannot_copy_outside_either_stage_root(
    shell_stage_fixture, table, name
):
    plan, allowed, _calls = shell_stage_fixture
    manifest = {**plan.manifest, table: {name: "a" * 64}}
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.prepare_native_agent_stage(
            replace(plan, manifest=manifest),
            staging_root=allowed / "stage",
            allowed_parent=allowed,
        )
    assert caught.value.code == (
        "native_release_manifest_files_invalid"
        if table == "runtime_files"
        else "native_release_manifest_assets_invalid"
    )
    assert not (allowed / "escape.txt").exists()


def test_legacy_phase_cannot_be_staged_even_with_a_manually_fabricated_plan(
    shell_stage_fixture,
):
    plan, allowed, _calls = shell_stage_fixture
    old = replace(plan, identity={**plan.identity, "phase": "phase5b_official_turn"})
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.prepare_native_agent_stage(
            old, staging_root=allowed / "stage", allowed_parent=allowed
        )
    assert caught.value.code == "native_release_phase_retired"
    assert not (allowed / "stage").exists()
