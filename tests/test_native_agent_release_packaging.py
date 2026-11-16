from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from genshin_app.version import APP_VERSION
from scripts import build_dist
from scripts import native_agent_release as release


@pytest.fixture(autouse=True)
def accepted_pin_fixture(monkeypatch):
    # A test-specific independent pin; never mutates the compiled release pin.
    monkeypatch.setattr(release, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", "f" * 64)


def test_direct_cohort_uses_the_reviewed_package_not_a_fixed_legacy_count():
    package = json.loads(
        (Path(__file__).parents[1] / "agent-runtime/package.json").read_text(
            encoding="utf-8",
        ),
    )

    assert package["dependencies"]
    assert not hasattr(release, "_EXPECTED_DIRECT_DEPENDENCIES")
    assert release.RUNTIME_MANIFEST == "dsh-client-shell-runtime-manifest.json"


def _make_dependency_metadata_fixture(runtime: Path) -> dict:
    installed = runtime / "node_modules/example"
    installed.mkdir(parents=True)
    (runtime / "package.json").write_text(
        json.dumps({"dependencies": {"example": "1.2.3"}}),
        encoding="utf-8",
    )
    (installed / "package.json").write_bytes(
        b'{"name":"example","version":"1.2.3"}\n',
    )
    state = {
        "included": {"dependencies": True},
        "nodeLinker": "hoisted",
        "packageManager": release.NATIVE_AGENT_PACKAGE_MANAGER,
        "pendingBuilds": [],
        "prunedAt": "2026-11-06T00:00:00Z",
        "storeDir": r"D:\pnpm-store\v11",
        "virtualStoreDir": r"D:\checkout\node_modules\.pnpm",
    }
    (runtime / "node_modules/.modules.yaml").write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )
    (runtime / "node_modules/.pnpm-workspace-state-v1.json").write_text(
        json.dumps({"projects": {r"D:\checkout": {"hasModulesDir": True}}}),
        encoding="utf-8",
    )
    return state


def test_node_resolution_never_scans_path(tmp_path):
    fake_path_node = tmp_path / "on-path/node.exe"
    fake_path_node.parent.mkdir()
    fake_path_node.write_bytes(b"path-node")

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.resolve_explicit_node(
            None,
            environment={"PATH": str(fake_path_node.parent)},
        )
    assert caught.value.code == "native_release_node_required"


def test_controlled_environment_and_cli_are_the_only_node_sources(tmp_path):
    selected = tmp_path / "selected.exe"
    selected.write_bytes(b"selected")
    fallback = tmp_path / "fallback.exe"
    fallback.write_bytes(b"fallback")

    assert release.resolve_explicit_node(
        None,
        environment={release.CONTROLLED_NODE_ENV: str(fallback)},
    ) == fallback.resolve()
    assert release.resolve_explicit_node(
        selected,
        environment={release.CONTROLLED_NODE_ENV: str(fallback)},
    ) == selected.resolve()


def test_build_plan_is_path_free_and_accepts_aligned_product_runtime_version(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "agent-runtime"
    runtime.mkdir()
    (tmp_path / "frontend").mkdir()
    manifest_path = runtime / release.RUNTIME_MANIFEST
    manifest_path.write_text("{}", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    identity = release.release_identity()
    raw_manifest = {
        **identity,
        "app_version": APP_VERSION,
        "runtime_files": {"package.json": "a" * 64},
        "runtime_closure_hash": "b" * 64,
        "dependency_lock_hash": "c" * 64,
        "asset_files": {"dist/index.html": "d" * 64},
        "asset_closure_hash": "e" * 64,
        "dependency_cohort": {},
    }

    monkeypatch.setattr(
        release,
        "_read_node_version",
        lambda _node, **_kwargs: identity["node_version"],
    )
    monkeypatch.setattr(
        release,
        "_sha256_file",
        lambda path: (
            identity["node_binary_sha256"]
            if Path(path).name == "node.exe"
            else identity["manifest_sha256"]
        ),
    )
    monkeypatch.setattr(
        release,
        "_load_dsh_client_shell_manifest",
        lambda *_args, **_kwargs: {"manifest": raw_manifest},
    )
    monkeypatch.setattr(
        release,
        "_validate_dependency_closure",
        lambda _root, **_kwargs: {
            "mode": identity["dependency_mode"],
            "direct_dependencies": 22,
            "package_manager": identity["package_manager"],
            "files": 2,
            "directories": 1,
            "links": 0,
            "bytes": 10,
        },
    )
    monkeypatch.setattr(
        release,
        "_run_dependency_runtime_gate",
        lambda *_args, **_kwargs: {
            "direct_resolutions": 22,
            "production_imports": 4,
        },
    )

    plan = release.create_native_agent_build_plan(
        project_root=tmp_path,
        native_node=node,
        environment={"PATH": str(tmp_path / "untrusted")},
    )
    public = plan.public()

    assert public["manifest_sha256"] == identity["manifest_sha256"]
    assert public["network_downloads"] is False
    assert public["path_discovery"] is False
    assert public["state"] == "validated"
    assert public["release_ready"] is True
    assert public["product_app_version"] == public["runtime_app_version"] == APP_VERSION
    assert public["release_blockers"] == []
    assert str(tmp_path) not in json.dumps(public)


def test_node_version_check_drops_path_and_node_startup_hooks(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": "v24.19.0\n", "stderr": ""},
        )()

    monkeypatch.setattr(release.subprocess, "run", run)
    version = release._read_node_version(
        node,
        environment={
            "SYSTEMROOT": r"C:\Windows",
            "TEMP": r"C:\Temp",
            "PATH": r"C:\untrusted",
            "NODE_OPTIONS": r"--require=C:\untrusted\preload.js",
            "NODE_PATH": r"C:\untrusted\modules",
        },
    )

    assert version == "24.19.0"
    assert captured["command"] == [str(node), "--version"]
    assert captured["environment"] == {
        "SYSTEMROOT": r"C:\Windows",
        "TEMP": r"C:\Temp",
    }


def test_dependency_validation_requires_exact_direct_versions_and_lock_state(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "agent-runtime"
    installed = runtime / "node_modules/example"
    installed.mkdir(parents=True)
    (runtime / "package.json").write_text(
        json.dumps({"dependencies": {"example": "1.2.3"}}),
        encoding="utf-8",
    )
    (installed / "package.json").write_text(
        json.dumps({"name": "example", "version": "1.2.3"}),
        encoding="utf-8",
    )
    (runtime / "node_modules/.modules.yaml").write_text(
        json.dumps({
            "packageManager": release.NATIVE_AGENT_PACKAGE_MANAGER,
            "nodeLinker": "hoisted",
            "pendingBuilds": [],
        }),
        encoding="utf-8",
    )

    result = release._validate_dependency_closure(runtime)

    assert result["direct_dependencies"] == 1
    assert result["mode"] == "locked-full-production-closure"
    assert result["metadata_normalization"] == "pnpm-release-metadata-v2"
    assert result["files"] == 2

    (installed / "package.json").write_text(
        json.dumps({"name": "example", "version": "1.2.4"}),
        encoding="utf-8",
    )
    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(runtime)
    assert caught.value.code == "native_release_dependency_version_mismatch"


def test_source_projects_install_metadata_without_mutating_it_and_metrics_match_stage(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    _make_dependency_metadata_fixture(source)
    modules_path = source / "node_modules/.modules.yaml"
    workspace_path = source / "node_modules/.pnpm-workspace-state-v1.json"
    original_modules = modules_path.read_bytes()
    original_workspace = workspace_path.read_bytes()

    source_projection = release._validate_dependency_closure(source)

    stage = tmp_path / "stage"
    shutil.copytree(source, stage)
    release._materialize_dependency_release_metadata(stage / "node_modules")
    staged = release._validate_dependency_closure(
        stage,
        require_canonical_release_metadata=True,
    )

    assert source_projection == staged
    assert source_projection["metadata_normalization"] == (
        "pnpm-release-metadata-v2"
    )
    assert source_projection["files"] == 2
    assert source_projection["directories"] == 1
    assert source_projection["bytes"] == 146
    assert source_projection["tree_sha256"] == (
        "dba38c6cd41fe72b0509e27782584b294d2746209c7a6320d5529c287879064a"
    )
    assert modules_path.read_bytes() == original_modules
    assert workspace_path.read_bytes() == original_workspace
    staged_modules = stage / "node_modules/.modules.yaml"
    assert staged_modules.read_bytes() == release._canonical_json_bytes(
        json.loads(staged_modules.read_text(encoding="utf-8")),
    )
    assert b"D:\\" not in staged_modules.read_bytes()
    assert not (stage / "node_modules/.pnpm-workspace-state-v1.json").exists()


def test_release_stages_shim_projection_without_modifying_installed_source(tmp_path):
    source = tmp_path / "source"
    _make_dependency_metadata_fixture(source)
    bin_dir = source / "node_modules/.bin"
    bin_dir.mkdir()
    wrapper = b'#!/bin/sh\nexec node "$basedir/../example/index.js" "$@"\n'
    installed = wrapper + b"# cmd-shim-target=D:/fixture-build/example/index.js\n"
    (bin_dir / "example").write_bytes(installed)
    (bin_dir / "example.CMD").write_bytes(b"@ECHO OFF\r\nnode ..\\example\\index.js %*\r\n")
    stage = tmp_path / "stage"
    shutil.copytree(source, stage)
    stage_modules = stage / "node_modules"
    release._materialize_dependency_release_metadata(stage_modules)

    assert (bin_dir / "example").read_bytes() == installed
    assert (stage_modules / ".bin/example").read_bytes() == wrapper
    assert sorted(p.name for p in (stage_modules / ".bin").iterdir()) == ["example", "example.cmd"]
    assert release._validate_dependency_closure(source) == release._validate_dependency_closure(
        stage, require_canonical_release_metadata=True)
    (stage_modules / ".bin/example").write_bytes(installed)
    with pytest.raises(release.NativeAgentReleaseError, match="native_release_dependency_shim_noncanonical"):
        release._validate_dependency_closure(stage, require_canonical_release_metadata=True)


def test_packaged_metadata_rejects_reinserted_install_host_path(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "runtime"
    _make_dependency_metadata_fixture(runtime)
    node_modules = runtime / "node_modules"
    release._materialize_dependency_release_metadata(node_modules)
    modules_path = node_modules / ".modules.yaml"
    state = json.loads(modules_path.read_text(encoding="utf-8"))
    state["storeDir"] = r"D:\pnpm-store\v11"
    modules_path.write_bytes(release._canonical_json_bytes(state))

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(
            runtime,
            require_canonical_release_metadata=True,
        )
    assert caught.value.code == "native_release_dependency_metadata_noncanonical"


def test_packaged_metadata_rejects_noncanonical_json(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _make_dependency_metadata_fixture(runtime)
    node_modules = runtime / "node_modules"
    release._materialize_dependency_release_metadata(node_modules)
    modules_path = node_modules / ".modules.yaml"
    state = json.loads(modules_path.read_text(encoding="utf-8"))
    modules_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(
            runtime,
            require_canonical_release_metadata=True,
        )
    assert caught.value.code == "native_release_dependency_metadata_noncanonical"


def test_packaged_metadata_rejects_workspace_install_state(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _make_dependency_metadata_fixture(runtime)
    node_modules = runtime / "node_modules"
    release._materialize_dependency_release_metadata(node_modules)
    (node_modules / ".pnpm-workspace-state-v1.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(
            runtime,
            require_canonical_release_metadata=True,
        )
    assert caught.value.code == "native_release_dependency_workspace_state_present"


@pytest.mark.parametrize(
    "host_path",
    [r"D:\future-cache", r"\\builder-host\pnpm-share\cache"],
)
def test_release_metadata_recursively_rejects_unknown_host_paths(
    tmp_path,
    monkeypatch,
    host_path,
):
    runtime = tmp_path / "runtime"
    _make_dependency_metadata_fixture(runtime)
    node_modules = runtime / "node_modules"
    release._materialize_dependency_release_metadata(node_modules)
    modules_path = node_modules / ".modules.yaml"
    state = json.loads(modules_path.read_text(encoding="utf-8"))
    state["futureMetadata"] = {"nested": [{"hostCache": host_path}]}
    modules_path.write_bytes(release._canonical_json_bytes(state))

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(
            runtime,
            require_canonical_release_metadata=True,
        )
    assert caught.value.code == "native_release_dependency_metadata_host_path"


def test_dependency_link_cannot_escape_the_locked_tree(tmp_path):
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = node_modules / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前Windows测试账户不能创建符号链接")

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_link_graph(node_modules.resolve())
    assert caught.value.code == "native_release_dependency_reparse_unsupported"


def test_stage_keeps_release_pin_outside_runtime_and_is_revalidated(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "source/agent-runtime"
    (runtime / "src").mkdir(parents=True)
    (runtime / "node_modules/example").mkdir(parents=True)
    (runtime / "src/main.mjs").write_text("export {};\n", encoding="utf-8")
    (runtime / "node_modules/example/index.js").write_text("", encoding="utf-8")
    raw_modules = json.dumps({
        "packageManager": release.NATIVE_AGENT_PACKAGE_MANAGER,
        "nodeLinker": "hoisted",
        "pendingBuilds": [],
        "storeDir": r"D:\pnpm-store",
        "virtualStoreDir": r"D:\source\node_modules\.pnpm",
    }, indent=2).encode("utf-8")
    (runtime / "node_modules/.modules.yaml").write_bytes(raw_modules)
    (runtime / "node_modules/.pnpm-workspace-state-v1.json").write_text(
        json.dumps({"projects": {r"D:\source": {"hasModulesDir": True}}}),
        encoding="utf-8",
    )
    manifest_path = runtime / release.RUNTIME_MANIFEST
    manifest_path.write_text('{"manifest":true}\n', encoding="utf-8")
    assets = tmp_path / "source/frontend"
    (assets / "dist").mkdir(parents=True)
    (assets / "dist/index.html").write_text("<!doctype html>", encoding="utf-8")
    node = tmp_path / "source/node.exe"
    node.write_bytes(b"node-binary")
    identity = {
        **release.release_identity(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "node_binary_sha256": hashlib.sha256(node.read_bytes()).hexdigest(),
    }
    manifest = {
        "runtime_files": {"src/main.mjs": "a" * 64},
        "runtime_closure_hash": "b" * 64,
        "dependency_lock_hash": "c" * 64,
        "asset_files": {"dist/index.html": "d" * 64},
        "asset_closure_hash": "e" * 64,
        "dependency_cohort": {},
    }
    plan = release.NativeAgentBuildPlan(
        runtime_root=runtime,
        assets_root=assets,
        node_executable=node,
        manifest_path=manifest_path,
        manifest=manifest,
        identity=identity,
        dependencies={"mode": identity["dependency_mode"]},
        product_app_version="0.45.0",
        runtime_app_version="0.45.0",
        release_blockers=(),
    )
    monkeypatch.setattr(
        release,
        "_load_dsh_client_shell_manifest",
        lambda *_a, **_k: {"manifest": manifest},
    )
    monkeypatch.setattr(
        release,
        "_validate_dependency_closure",
        lambda _root, **_kwargs: {"mode": identity["dependency_mode"]},
    )
    monkeypatch.setattr(
        release,
        "_run_dependency_runtime_gate",
        lambda *_args, **_kwargs: {},
    )
    allowed = tmp_path / "build"
    allowed.mkdir()
    stage = release.prepare_native_agent_stage(
        plan,
        staging_root=allowed / "stage",
        allowed_parent=allowed,
    )

    assert (stage / "agent-runtime/node.exe").read_bytes() == b"node-binary"
    assert (stage / "agent-client-assets/dist/index.html").read_text("utf8") == "<!doctype html>"
    identity_path = stage / "native-agent-release/release-identity.json"
    assert json.loads(identity_path.read_text(encoding="utf-8")) == identity
    assert not (stage / "agent-runtime/release-identity.json").exists()
    assert (runtime / "node_modules/.modules.yaml").read_bytes() == raw_modules
    assert (runtime / "node_modules/.pnpm-workspace-state-v1.json").is_file()
    staged_modules = stage / "agent-runtime/node_modules/.modules.yaml"
    staged_state = json.loads(staged_modules.read_text(encoding="utf-8"))
    assert "storeDir" not in staged_state
    assert "virtualStoreDir" not in staged_state
    assert staged_modules.read_bytes() == release._canonical_json_bytes(staged_state)
    assert not (
        stage / "agent-runtime/node_modules/.pnpm-workspace-state-v1.json"
    ).exists()

    release.cleanup_native_agent_stage(stage, allowed_parent=allowed)
    assert not stage.exists()


def test_stage_rejects_a_target_outside_the_controlled_build_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    plan = release.NativeAgentBuildPlan(
        runtime_root=tmp_path,
        node_executable=tmp_path / "node.exe",
        manifest_path=tmp_path / "manifest.json",
        manifest={},
        identity={},
        dependencies={},
        product_app_version="0.44.0",
        runtime_app_version="0.45.0",
        release_blockers=(),
    )

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.prepare_native_agent_stage(
            plan,
            staging_root=tmp_path / "outside",
            allowed_parent=allowed,
        )
    assert caught.value.code == "native_release_stage_outside_build_root"


def test_staging_is_blocked_until_product_and_runtime_versions_align(tmp_path):
    plan = release.NativeAgentBuildPlan(
        runtime_root=tmp_path,
        node_executable=tmp_path / "node.exe",
        manifest_path=tmp_path / "manifest.json",
        manifest={},
        identity={},
        dependencies={},
        product_app_version="0.44.0",
        runtime_app_version="0.45.0",
        release_blockers=("native_release_app_version_mismatch",),
    )

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release.prepare_native_agent_stage(
            plan,
            staging_root=tmp_path / "allowed/stage",
            allowed_parent=tmp_path / "allowed",
        )
    assert caught.value.code == "native_release_plan_blocked"
    assert caught.value.cause_code == "native_release_app_version_mismatch"
    assert not (tmp_path / "allowed/stage").exists()


def test_pyinstaller_spec_requires_validated_native_stage():
    spec = (Path(__file__).resolve().parents[1] / "genshin_app.spec").read_text(
        encoding="utf-8",
    )

    assert 'os.environ.get("GENSHIN_NATIVE_AGENT_STAGE"' in spec
    assert '(str(native_runtime), "agent-runtime")' in spec
    assert '(str(native_release), "native-agent-release")' in spec
    assert "validate_staged_native_agent_runtime(native_stage)" in spec
    assert '(str(native_assets), "agent-client-assets")' in spec
    assert "phase5b-official-turn-runtime-manifest" not in spec


def test_pyinstaller_analysis_keeps_the_public_compatibility_tool_bridge():
    spec_path = Path(__file__).resolve().parents[1] / "genshin_app.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis"]
    assert len(calls) == 1
    expression = next(keyword.value for keyword in calls[0].keywords if keyword.arg == "hiddenimports")
    # Evaluate only the effective Analysis keyword, without executing the spec,
    # its build/admission side effects or PyInstaller. Sentinels also ensure
    # explicit compatibility imports do not discard the two existing closures.
    hidden = eval(compile(ast.Expression(expression), str(spec_path), "eval"), {"__builtins__": {}}, {
        "webview_hidden": ["test.webview_closure"], "engine_runtime_modules": ["test.engine_closure"],
        "app_runtime_modules": ["test.app_closure"],
    })
    assert "genshin_app.agent_chat.native_app_tool_bridge" in hidden
    assert "test.webview_closure" in hidden and "test.engine_closure" in hidden
    assert "test.app_closure" in hidden


def test_pyinstaller_spec_allows_a_new_app_pair_to_reuse_an_accepted_database():
    spec = (Path(__file__).resolve().parents[1] / "genshin_app.spec").read_text(
        encoding="utf-8",
    )

    # The immutable database manifest keeps the App/pair identity from the
    # release that first published it.  A later App proves reuse through the
    # new pair manifest's exact database-manifest hash; it must not require the
    # old database manifest to be rewritten with the new App identity.
    assert (
        'pair_manifest.get("release_pair_id") != '
        'database_manifest.get("release_pair_id")'
    ) not in spec
    assert (
        'database_manifest.get("release_pair_id")\n'
        '    != version_values["RELEASE_PAIR_ID"]'
    ) not in spec
    assert 'database_pair.get("manifest_sha256") != database_manifest_sha256' in spec
    assert (
        'pair_manifest.get("release_pair_id") != '
        'version_values["RELEASE_PAIR_ID"]'
    ) in spec


def test_dependency_validation_rejects_an_isolated_layout(tmp_path, monkeypatch):
    runtime = tmp_path / "agent-runtime"
    installed = runtime / "node_modules/example"
    installed.mkdir(parents=True)
    (runtime / "package.json").write_text(
        json.dumps({"dependencies": {"example": "1.2.3"}}),
        encoding="utf-8",
    )
    (installed / "package.json").write_text(
        json.dumps({"name": "example", "version": "1.2.3"}),
        encoding="utf-8",
    )
    (runtime / "node_modules/.modules.yaml").write_text(
        json.dumps({
            "packageManager": release.NATIVE_AGENT_PACKAGE_MANAGER,
            "nodeLinker": "isolated",
            "pendingBuilds": [],
        }),
        encoding="utf-8",
    )

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._validate_dependency_closure(runtime)
    assert caught.value.code == "native_release_dependency_linker_mismatch"


def test_dependency_runtime_gate_resolves_all_and_imports_production_modules(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "agent-runtime"
    runtime.mkdir()
    dependencies = {
        "@deepseek-ai/dsh": "0.2.1-rc.5",
        **{
            f"example-{index}": "1.0.0"
            for index in range(3)
        },
    }
    (runtime / "package.json").write_text(
        json.dumps({"dependencies": dependencies}),
        encoding="utf-8",
    )
    receipt = {
        "resolved": [
            {
                "name": name,
                "status": (
                    "present_no_root_export"
                    if name == "@deepseek-ai/dsh"
                    else "resolved"
                ),
            }
            for name in sorted(dependencies)
        ],
        "imported": list(release._PRODUCTION_IMPORTS),
    }
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(receipt),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(release.subprocess, "run", run)
    result = release._run_dependency_runtime_gate(
        runtime,
        tmp_path / "node.exe",
        environment={
            "SYSTEMROOT": r"C:\Windows",
            "PATH": r"C:\untrusted",
            "NODE_OPTIONS": "--require=untrusted.js",
        },
    )

    assert result == {
        "direct_resolutions": len(dependencies),
        "production_imports": len(release._PRODUCTION_IMPORTS),
    }
    assert captured["cwd"] == runtime
    assert captured["environment"] == {"SYSTEMROOT": r"C:\Windows"}
    assert captured["command"][1:3] == ["--input-type=module", "--eval"]


def test_dependency_runtime_gate_fails_closed_on_node_resolution_error(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "agent-runtime"
    runtime.mkdir()
    (runtime / "package.json").write_text(
        json.dumps({
            "dependencies": {
                "@deepseek-ai/dsh": "0.2.1-rc.5",
                **{f"example-{index}": "1.0.0" for index in range(21)},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "ERR_MODULE_NOT_FOUND",
            },
        )(),
    )

    with pytest.raises(release.NativeAgentReleaseError) as caught:
        release._run_dependency_runtime_gate(runtime, tmp_path / "node.exe")
    assert caught.value.code == "native_release_dependency_runtime_failed"


def test_packaged_runtime_revalidates_identity_layout_and_node_gate(
    tmp_path,
    monkeypatch,
):
    package = tmp_path / "GenshinCalc"
    runtime = package / "_internal/agent-runtime"
    release_root = package / "_internal/native-agent-release"
    runtime.mkdir(parents=True)
    release_root.mkdir(parents=True)
    (package / "_internal/agent-client-assets").mkdir(parents=True)
    manifest_path = runtime / release.RUNTIME_MANIFEST
    manifest_path.write_text("{}", encoding="utf-8")
    (runtime / "node.exe").write_bytes(b"node")
    identity = {
        **release.release_identity(),
        "manifest_sha256": "a" * 64,
        "node_binary_sha256": "b" * 64,
    }
    manifest = {
        "runtime_closure_hash": "c" * 64,
        "dependency_lock_hash": "d" * 64,
        "asset_files": {"dist/index.html": "e" * 64},
        "asset_closure_hash": "f" * 64,
        "dependency_cohort": {},
    }
    dependencies = {
        "mode": identity["dependency_mode"],
        "direct_dependencies": 22,
        "package_manager": identity["package_manager"],
        "node_linker": "hoisted",
        "metadata_normalization": "pnpm-release-metadata-v2",
        "reparse_points": 0,
        "files": 10,
        "directories": 2,
        "links": 0,
        "bytes": 100,
        "direct_resolutions": 22,
        "production_imports": 4,
    }
    plan = release.NativeAgentBuildPlan(
        runtime_root=tmp_path / "source",
        node_executable=tmp_path / "source-node.exe",
        manifest_path=tmp_path / "source-manifest.json",
        manifest=manifest,
        identity=identity,
        dependencies=dependencies,
        product_app_version="0.45.0",
        runtime_app_version="0.45.0",
        release_blockers=(),
    )
    (release_root / release.RELEASE_IDENTITY_FILENAME).write_text(
        json.dumps(identity),
        encoding="utf-8",
    )
    (release_root / release.BUILD_PLAN_FILENAME).write_text(
        json.dumps(plan.public()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release,
        "_sha256_file",
        lambda path: "a" * 64 if Path(path).name == release.RUNTIME_MANIFEST else "b" * 64,
    )
    monkeypatch.setattr(
        release,
        "_read_node_version",
        lambda *_args, **_kwargs: identity["node_version"],
    )
    monkeypatch.setattr(
        release,
        "_load_dsh_client_shell_manifest",
        lambda *_args, **_kwargs: {"manifest": manifest},
    )
    monkeypatch.setattr(
        release,
        "_validate_dependency_closure",
        lambda _root, **_kwargs: {
            key: value
            for key, value in dependencies.items()
            if key not in {"direct_resolutions", "production_imports"}
        },
    )
    monkeypatch.setattr(
        release,
        "_run_dependency_runtime_gate",
        lambda *_args, **_kwargs: {
            "direct_resolutions": 22,
            "production_imports": 4,
        },
    )

    result = release.validate_packaged_native_agent_runtime(
        plan,
        package_root=package,
    )
    assert result["dependencies"] == dependencies


def test_build_dist_runs_package_gate_before_manifest_and_inno_inputs():
    source = Path(build_dist.__file__).read_text(encoding="utf-8")

    gate = source.index("validate_packaged_native_agent_runtime(")
    package_manifest = source.index("write_package_file_manifest(dist)")
    inno = source.index("_write_inno_build_inputs(", gate)
    assert gate < package_manifest < inno


def test_build_temp_directory_inherits_workspace_acl_and_is_cleaned(tmp_path):
    build_root = tmp_path / "build-py"

    with build_dist._managed_build_temp_dir(
        build_root=build_root,
        prefix="packaged-startup-rebind-",
    ) as root:
        recorded_root = root
        assert root.parent == build_root.resolve()
        assert root.name.startswith("packaged-startup-rebind-")
        profile = root / "profile"
        profile.mkdir()
        (profile / "probe.json").write_text("{}\n", encoding="utf-8")

    assert not recorded_root.exists()


def test_failed_candidate_cleanup_is_scoped_to_exact_dist_child(tmp_path):
    allowed = tmp_path / "dist"
    candidate = allowed / "release/GenshinCalc"
    candidate.mkdir(parents=True)
    (candidate / "partial.txt").write_text("partial", encoding="utf-8")

    build_dist._cleanup_failed_candidate(dist=candidate, allowed_root=allowed)

    assert allowed.is_dir()
    assert not candidate.exists()
    with pytest.raises(ValueError):
        build_dist._cleanup_failed_candidate(dist=allowed, allowed_root=allowed)


def test_release_npmrc_pins_the_physical_hoisted_layout():
    npmrc = (
        Path(__file__).resolve().parents[1] / "agent-runtime/.npmrc"
    ).read_text(encoding="utf-8")
    assert "node-linker=hoisted" in npmrc.splitlines()
