"""Build policy keeps all static gates and never invents an App startup pass."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_dist
from tests.test_build_candidate_database import builder as builder  # noqa: PLC0414


@pytest.mark.parametrize("predecessor", [None, {"pair_sequence": 4}])
def test_no_app_execution_never_enters_either_smoke_or_subprocess(monkeypatch, predecessor):
    def forbidden(*_args, **_kwargs):
        pytest.fail("no-App policy must not enter an executable startup path")

    monkeypatch.setattr(build_dist, "_run_packaged_clean_startup_smoke", forbidden)
    monkeypatch.setattr(build_dist, "_run_packaged_startup_rebind_smoke", forbidden)
    monkeypatch.setattr(build_dist.subprocess, "run", forbidden)
    result = build_dist._qualify_packaged_app_startup(
        execute_app=False, package_root=Path("not-opened"), database_manifest={},
        pair_manifest={"pair_sequence": 5, "release_pair_id": "pair-next"},
        predecessor_pair=predecessor)
    assert result == {"status": "not_run_user_acceptance", "mode": "static_no_app_execution",
        "app_executed": False, "prior_pair_sequence": 4 if predecessor else None,
        "current_pair_sequence": 5, "release_pair_id": "pair-next"}


@pytest.mark.parametrize("predecessor", [None, {"pair_sequence": 4}])
def test_default_execution_uses_exact_original_smoke_once(monkeypatch, predecessor):
    expected, calls = {"status": "passed", "fixture": True}, []

    def clean(**kwargs):
        calls.append(("clean", kwargs))
        return expected

    def rebind(**kwargs):
        calls.append(("rebind", kwargs))
        return expected

    monkeypatch.setattr(build_dist, "_run_packaged_clean_startup_smoke", clean)
    monkeypatch.setattr(build_dist, "_run_packaged_startup_rebind_smoke", rebind)
    pair, database, root = {}, {}, Path("package")
    actual = build_dist._qualify_packaged_app_startup(execute_app=True, package_root=root,
        database_manifest=database, pair_manifest=pair, predecessor_pair=predecessor)
    assert actual is expected and len(calls) == 1
    assert calls[0][0] == ("rebind" if predecessor else "clean")
    assert calls[0][1]["package_root"] is root
    assert calls[0][1]["pair_manifest"] is pair
    if predecessor:
        assert calls[0][1]["predecessor_pair"] is predecessor
        assert calls[0][1]["database_manifest"] is database


def test_default_startup_error_cannot_be_relabelled_as_not_run(monkeypatch):
    failure = ValueError("startup fixture failure")

    def fail(**_kwargs):
        raise failure

    monkeypatch.setattr(build_dist, "_run_packaged_clean_startup_smoke", fail)
    with pytest.raises(ValueError) as caught:
        build_dist._qualify_packaged_app_startup(execute_app=True, package_root=Path("package"),
            database_manifest={}, pair_manifest={}, predecessor_pair=None)
    assert caught.value is failure


@pytest.fixture
def completed_builder(builder, monkeypatch):
    """Real main/publish/inventory; fake PyInstaller and domain gate fixtures."""
    module, root = builder
    database = root / "data/genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"same fixture database")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest = {"database_release_id": module.app_version.DATABASE_RELEASE_ID,
        "release_sequence": module.app_version.DATABASE_RELEASE_SEQUENCE,
        "channel": module.app_version.DATABASE_RELEASE_CHANNEL,
        "logic_profile": module.app_version.DATABASE_LOGIC_PROFILE,
        "data_contract_version": module.app_version.DATABASE_DATA_CONTRACT_VERSION,
        "database": {"sha256": digest, "size_bytes": database.stat().st_size,
                     "meta_fingerprint": "a" * 64}}
    state = {"calls": [], "pair": None, "manifest": manifest, "root": root,
             "dist_root": root / "dist/policy-fixture"}

    def pair(_manifest, **kwargs):
        state["pair"] = {"release_pair_id": kwargs["release_pair_id"],
            "pair_sequence": kwargs["pair_sequence"],
            "app": {"artifact_manifest": {"sha256": kwargs["app_artifact_manifest_sha256"]}}}
        return state["pair"]

    monkeypatch.setattr(module, "create_database_release_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(module, "load_database_release_manifest", lambda *_a: manifest)
    monkeypatch.setattr(module, "create_release_pair_manifest", pair)
    monkeypatch.setattr(module, "load_release_pair_manifest", lambda *_a: state["pair"])
    monkeypatch.setattr(module, "read_database_identity", lambda *_a: manifest["database"])
    monkeypatch.setattr(module, "release_pair_matches_database_manifest", lambda *_a: True)

    def prepare(_plan, **kwargs):
        staging = Path(kwargs["staging_root"])
        (staging / "agent-runtime/node_modules").mkdir(parents=True)
        supply = staging / "native-agent-release/supply-chain"
        supply.mkdir(parents=True)
        for name in (module.SBOM_FILENAME, module.LICENSE_MANIFEST_FILENAME, module.LICENSE_REVIEW_FILENAME,
                     module.NOTICE_FILENAME, module.SUPPLY_CHAIN_RECEIPT_FILENAME):
            (supply / name).write_text("fixture\n", encoding="utf8")
        return staging

    def run(command, **_kwargs):
        assert command[1:4] == ["-m", "PyInstaller", "genshin_app.spec"]
        state["calls"].append("pyinstaller")
        package = state["dist_root"] / "GenshinCalc/app"
        bundled = package / "_internal/bundled"
        bundled.mkdir(parents=True)
        (package / "_internal/agent-runtime/node_modules").mkdir(parents=True)
        (bundled / "genshin.db").write_bytes(database.read_bytes())
        shutil.copytree(Path(_kwargs["env"]["GENSHIN_NATIVE_AGENT_STAGE"]) /
            "native-agent-release/supply-chain", package / "_internal/native-agent-release/supply-chain")
        (package / "GenshinCalc.exe").write_bytes(b"not an executable")
        (package / "GenshinCalcGUI.exe").write_bytes(b"not an executable")
        shutil.copytree(root / "src/genshin_runtime", package / "_internal/genshin_runtime")
        return SimpleNamespace(returncode=0)

    def native_gate(*_args, **_kwargs):
        state["calls"].append("native_gate")
        return {"state": "fixture_verified"}

    def updater(**_kwargs):
        state["calls"].append("updater_build")
        path = state["dist_root"] / module.UPDATER_ARTIFACT_DIRECTORY / module.UPDATER_EXE_FILENAME
        path.parent.mkdir()
        path.write_bytes(b"not an updater executable")
        receipt_path, receipt = module._write_updater_artifact_receipt(path)
        return 0, path, receipt_path, receipt

    def smoke(**_kwargs):
        state["calls"].append("app_smoke_mock")
        return {"status": "passed", "current_pair_sequence": state["pair"]["pair_sequence"]}

    monkeypatch.setattr(module, "prepare_native_agent_stage", prepare)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "validate_packaged_native_agent_runtime", native_gate)
    # This orchestration fixture writes inert bytes, not PE/PYZ archives.
    # Real archive qualification has independent closure/CodeType tests.
    monkeypatch.setattr(module, "validate_engine_package_archive", lambda *_a, **_k: {
        "layer": "archive_inspection", "fixture": "inert_builder_orchestration"})
    monkeypatch.setattr(module, "_build_updater_artifact", updater)
    monkeypatch.setattr(module, "_run_packaged_clean_startup_smoke", smoke)
    monkeypatch.setattr(module, "_run_packaged_startup_rebind_smoke", smoke)
    (root / "skill").mkdir()
    (root / "skill/SKILL.md").write_text("fixture\n", encoding="utf8")
    version = root / "src/genshin_app/version.py"
    version.parent.mkdir(parents=True)
    version.write_text(f"APP_VERSION = {module.app_version.APP_VERSION!r}\n", encoding="utf8")
    resources = root / "src/genshin_runtime/resources"
    resources.mkdir(parents=True)
    (resources / "profiles.json").write_bytes(b"{}\n")
    (resources / "action_dispatch_profiles.json").write_bytes(b'{"fixture": true}\n')
    state["argv"] = ["build_dist.py", "--dist-root", "dist/policy-fixture", "--new-database-release"]
    return module, state


@pytest.mark.parametrize("no_app", [False, True])
def test_real_main_preserves_static_gates_and_records_actual_execution_policy(
    completed_builder, monkeypatch, capsys, no_app,
):
    module, state = completed_builder
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], *(["--no-app-execution"] if no_app else [])])
    assert module.main() == 0
    package = state["dist_root"] / "GenshinCalc/app"
    receipt_path = state["dist_root"] / "build-qualification.json"
    receipt = json.loads(receipt_path.read_text("utf8"))
    assert receipt_path.parent == package.parent.parent and not receipt_path.is_relative_to(package)
    assert state["calls"] == (["pyinstaller", "native_gate", "updater_build"] if no_app
        else ["pyinstaller", "native_gate", "app_smoke_mock", "updater_build"])
    expected = "not_run_user_acceptance" if no_app else "passed"
    assert receipt["app_startup"]["status"] == expected
    assert receipt["status"] == ("built_pending_user_acceptance" if no_app else "built_and_smoke_verified")
    assert receipt["static_checks"]["native_runtime"] == {"state": "fixture_verified"}
    resource_proof = receipt["static_checks"]["runtime_validation_resource_sha256"]
    assert resource_proof["genshin_runtime/resources/profiles.json"] == hashlib.sha256(b"{}\n").hexdigest()
    assert receipt["execution_authority"] == "none"
    actual = module.verify_package_file_manifest(package)
    assert actual["manifest_sha256"] == receipt["static_checks"]["package_inventory"]["manifest_sha256"]
    output = capsys.readouterr().out
    assert "not_run_user_acceptance" in output if no_app else "真实首次启动冒烟：passed" in output


@pytest.mark.parametrize("released_at", [None, "2026-11-13T12:00:00+00:00"])
def test_reused_database_keeps_its_date_while_both_pair_builds_use_release_date(
    completed_builder, monkeypatch, released_at,
):
    """Real CLI/main and manifest functions; only package build/PE gates are fixtures."""
    from genshin_db import release_profile
    from tests.test_database_release_migration import (
        _fake_identity,
        _fixture_database,
        _manifest,
    )

    module, state = completed_builder
    # The shared SQLite fixture models game version 7.0.0, not the live DB.
    monkeypatch.setattr(module.app_version, "DATABASE_RELEASE_ID", "db-7.0.0-r1")
    database = state["root"] / "data/genshin.db"
    database.unlink()  # Replace this test's inert bytes with its SQLite fixture.
    _fixture_database(database, upstream_commit="a" * 40,
                      annotation_prefix="accepted", semantic_marker=None)
    manifest = _manifest(database,
        release_id=module.app_version.DATABASE_RELEASE_ID,
        release_sequence=module.app_version.DATABASE_RELEASE_SEQUENCE,
        logic_profile=module.app_version.DATABASE_LOGIC_PROFILE)
    manifest["channel"] = module.app_version.DATABASE_RELEASE_CHANNEL
    accepted = database.with_name(module.DATABASE_MANIFEST_FILENAME)
    release_profile.write_database_release_manifest(accepted, manifest)
    manifest = release_profile.load_database_release_manifest(accepted)
    original_database = database.read_bytes()
    original_manifest = accepted.read_bytes()
    pairs = []

    def create_pair(*args, **kwargs):
        actual = release_profile.create_release_pair_manifest(*args, **kwargs)
        pairs.append(actual)
        state["pair"] = actual
        return actual

    inert_build = module.subprocess.run

    def package_fixture(command, **kwargs):
        result = inert_build(command, **kwargs)
        bundled = state["dist_root"] / "GenshinCalc/app/_internal/bundled"
        for filename in (module.DATABASE_MANIFEST_FILENAME, module.PAIR_MANIFEST_FILENAME):
            shutil.copyfile(state["root"] / "build-py" / filename, bundled / filename)
        return result

    monkeypatch.setattr(module, "create_database_release_manifest",
                        lambda *_a, **_k: pytest.fail("accepted DB must not be reissued"))
    for name in ("load_database_release_manifest", "load_release_pair_manifest",
                 "write_release_pair_manifest", "release_pair_matches_database_manifest"):
        monkeypatch.setattr(module, name, getattr(release_profile, name))
    monkeypatch.setattr(module, "read_database_identity", _fake_identity)
    monkeypatch.setattr(module, "create_release_pair_manifest", create_pair)
    monkeypatch.setattr(module.subprocess, "run", package_fixture)
    argv = [argument for argument in state["argv"] if argument != "--new-database-release"]
    argv += ["--accepted-database-manifest", str(accepted), "--no-app-execution"]
    if released_at is not None:
        argv += ["--released-at", released_at]
    monkeypatch.setattr(module.sys, "argv", argv)

    assert module.main() == 0
    assert len(pairs) == 2
    expected_date = manifest["released_at"] if released_at is None else released_at
    assert [pair["released_at"] for pair in pairs] == [expected_date, expected_date]
    assert pairs[0]["app"]["artifact_manifest"]["sha256"] == "0" * 64
    assert pairs[1]["app"]["artifact_manifest"]["sha256"] != "0" * 64
    assert pairs[0]["database"] == pairs[1]["database"]
    assert database.read_bytes() == original_database
    assert accepted.read_bytes() == original_manifest
    package = state["dist_root"] / "GenshinCalc/app/_internal/bundled"
    assert (package / "genshin.db").read_bytes() == original_database
    assert (package / module.DATABASE_MANIFEST_FILENAME).read_bytes() == original_manifest
    assert (state["root"] / "build-py" / module.DATABASE_MANIFEST_FILENAME).read_bytes() == original_manifest
    assert release_profile.load_release_pair_manifest(package / module.PAIR_MANIFEST_FILENAME) == pairs[1]


def test_no_app_policy_cannot_bypass_engine_dependency_failure(completed_builder, monkeypatch, capsys):
    module, state = completed_builder

    def fail(*_args, **_kwargs):
        raise module.EngineRuntimePackageError("engine_dynamic_dependency_missing",
            entry="GenshinCalc.exe", modules=["runtime.root"], raw_secret="must never print")

    monkeypatch.setattr(module, "validate_engine_package_archive", fail)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert not (state["dist_root"] / "GenshinCalc/app").exists()
    assert "native_gate" not in state["calls"]
    assert "app_smoke_mock" not in state["calls"]
    output = capsys.readouterr().out
    assert "engine_dynamic_dependency_missing" in output
    assert '"entry": "GenshinCalc.exe"' in output and '"modules": ["runtime.root"]' in output
    assert "raw_secret" not in output and "must never print" not in output


def test_no_app_policy_cannot_accept_a_missing_database_validation_file(completed_builder, monkeypatch, capsys):
    module, state = completed_builder
    inert_build = module.subprocess.run
    candidate = state["dist_root"] / "GenshinCalc/app"

    def run(command, **kwargs):
        result = inert_build(command, **kwargs)
        (candidate / "_internal/genshin_runtime/resources/action_dispatch_profiles.json").unlink()
        return result

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert state["calls"] == ["pyinstaller"]
    assert not candidate.exists()
    assert not (state["dist_root"] / "build-qualification.json").exists()
    assert "runtime_validation_resource_missing" in capsys.readouterr().out


def test_archive_failure_prints_only_closed_safe_entry_and_module_identities():
    error = build_dist.EngineRuntimePackageError("engine_package_source_mismatch",
        entry="GenshinCalcGUI.exe", module="genshin_app.calculation_coverage",
        raw_path="C:/private/unrelated", raw_secret="must never print")
    assert build_dist._engine_archive_public_details(error) == {
        "entry": "GenshinCalcGUI.exe", "module": "genshin_app.calculation_coverage"}
    error = build_dist.EngineRuntimePackageError("engine_changed_source_missing",
        entry="C:/private/App.exe", module="C:/private/source.py", modules=["good.module", "bad/path"])
    assert build_dist._engine_archive_public_details(error) == {}
    error.details = {"entry": [], "module": {}, "modules": [None]}
    assert build_dist._engine_archive_public_details(error) == {}


def test_no_app_policy_does_not_bypass_native_package_failure_or_keep_stale_success(
    completed_builder, monkeypatch,
):
    module, state = completed_builder
    state["dist_root"].mkdir(parents=True)
    receipt = state["dist_root"] / "build-qualification.json"
    receipt.write_text('{"status":"old_success"}', encoding="utf8")

    def fail(*_args, **_kwargs):
        raise module.NativeAgentReleaseError("fixture_native_gate_failed")

    monkeypatch.setattr(module, "validate_packaged_native_agent_runtime", fail)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert not receipt.exists()
    assert not (state["dist_root"] / "GenshinCalc/app").exists()
    assert "app_smoke_mock" not in state["calls"]


def test_directory_publication_runs_after_collect_before_stage_cleanup_and_native_gate(
    completed_builder, monkeypatch,
):
    module, state = completed_builder
    original = module.preserve_packaged_native_dependency_directories
    order = []

    def publish(**kwargs):
        assert state["calls"] == ["pyinstaller"]
        assert kwargs["staging_root"].is_dir()
        order.append("directory_publication")
        return original(**kwargs)

    def cleanup(*_args, **_kwargs):
        assert order == ["directory_publication"]
        order.append("stage_cleanup")

    monkeypatch.setattr(module, "preserve_packaged_native_dependency_directories", publish)
    monkeypatch.setattr(module, "cleanup_native_agent_stage", cleanup)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 0
    assert order == ["directory_publication", "stage_cleanup"]
    assert state["calls"] == ["pyinstaller", "native_gate", "updater_build"]


def test_directory_publication_failure_cannot_qualify_or_enter_native_gate(
    completed_builder, monkeypatch,
):
    module, state = completed_builder
    cleaned = []

    def fail(**_kwargs):
        raise module.NativeAgentReleaseError("native_release_directory_publication_source_count_mismatch")

    monkeypatch.setattr(module, "preserve_packaged_native_dependency_directories", fail)
    monkeypatch.setattr(module, "cleanup_native_agent_stage", lambda *_a, **_k: cleaned.append(True))
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert cleaned == [True]
    assert state["calls"] == ["pyinstaller"]
    assert not (state["dist_root"] / "build-qualification.json").exists()
    assert not (state["dist_root"] / "GenshinCalc/app").exists()


def test_layout_failure_preserves_relative_diagnostic_without_success(
    completed_builder, monkeypatch, capsys,
):
    module, state = completed_builder

    def fail(*_args, **_kwargs):
        error = module.NativeAgentReleaseError("native_release_package_dependency_layout_mismatch")
        error.diagnostic = {"contract": "genshin-native-dependency-layout-failure-v1",
            "expected": {"directories": 4}, "actual": {"directories": 1},
            "different_fields": ["directories"], "qualification": False}
        raise error

    monkeypatch.setattr(module, "validate_packaged_native_agent_runtime", fail)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    receipts = list((state["root"] / "build/native-agent-v2").glob("failure-*.json"))
    assert len(receipts) == 1
    report = json.loads(receipts[0].read_text("utf8"))
    assert report["phase"] == "packaged_runtime" and report["qualification"] is False
    assert report["different_fields"] == ["directories"]
    assert str(state["root"]) not in receipts[0].read_text("utf8")
    assert "原生Agent失败诊断: build/native-agent-v2/failure-" in capsys.readouterr().out
    assert not (state["dist_root"] / "build-qualification.json").exists()


def test_diagnostic_sink_failure_never_replaces_release_error(builder, monkeypatch):
    module, _root = builder
    error = module.NativeAgentReleaseError("native_release_package_dependency_layout_mismatch")
    error.diagnostic = {"qualification": False}

    def fail(*_args, **_kwargs):
        raise OSError("fixture diagnostic disk failure")

    monkeypatch.setattr(Path, "open", fail)
    module._record_native_release_failure(error, phase="packaged_runtime")
    assert error.code == "native_release_package_dependency_layout_mismatch"


def test_failed_qualification_publication_removes_candidate_and_installer_inputs(
    completed_builder, monkeypatch,
):
    module, state = completed_builder

    def fail(**_kwargs):
        raise OSError("fixture receipt failure")

    monkeypatch.setattr(module, "_write_build_qualification", fail)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert not (state["dist_root"] / "build-qualification.json").exists()
    assert not (state["dist_root"] / "GenshinCalc/app").exists()
    assert not (state["root"] / "build-py/app-layout.iss").exists()


def test_no_app_policy_still_rejects_invalid_final_inventory(completed_builder, monkeypatch):
    module, state = completed_builder

    def fail(*_args):
        raise module.SupplyChainError("fixture_inventory_invalid")

    monkeypatch.setattr(module, "verify_package_file_manifest", fail)
    monkeypatch.setattr(module.sys, "argv", [*state["argv"], "--no-app-execution"])
    assert module.main() == 1
    assert not (state["dist_root"] / "build-qualification.json").exists()
    assert not (state["dist_root"] / "GenshinCalc/app").exists()
    assert state["calls"] == ["pyinstaller", "native_gate"]


def test_atomic_qualification_failure_leaves_no_temporary_success(tmp_path, monkeypatch):
    def fail(*_args):
        raise OSError("fixture atomic publish failure")

    monkeypatch.setattr(build_dist.os, "replace", fail)
    with pytest.raises(OSError):
        build_dist._write_build_qualification(dist_root=tmp_path,
            pair_manifest={}, package_receipt={}, native_receipt={}, runtime_resource_hashes={},
            startup={"status": "not_run_user_acceptance"})
    assert list(tmp_path.iterdir()) == []
