"""G5 构建脚本：离线验证原生Agent闭包后构建PyInstaller one-dir。

用法：py -3.12 scripts/build_dist.py --native-node D:/path/to/node.exe
前置：pip install pyinstaller（dev 依赖）；data/genshin.db 已构建；精确
Node 24.19.x和agent-runtime/node_modules已经离线准备。也可只设置受控
GENSHIN_NATIVE_NODE，构建器绝不扫描PATH或下载依赖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "GenshinCalc"
UPDATER_ARTIFACT_DIRECTORY = "GenshinCalcUpdater"
UPDATER_EXE_FILENAME = "GenshinCalcUpdater.exe"
UPDATER_RECEIPT_FILENAME = "updater-artifact.json"
UPDATER_ARTIFACT_CONTRACT = "genshin-updater-artifact-v1"
UPDATER_REQUEST_SCHEMA_VERSION = 1
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from engine_runtime_release import (
    EngineRuntimePackageError,
    validate_engine_package_archive,
)
from native_agent_release import (
    NativeAgentReleaseError,
    cleanup_native_agent_stage,
    create_native_agent_build_plan,
    prepare_native_agent_stage,
    preserve_packaged_native_dependency_directories,
    validate_packaged_native_agent_runtime,
    write_build_plan,
)
from packaged_database_upgrade import (
    load_predecessor_database,
    run_packaged_database_upgrade_smoke,
)
from packaging_windows_runtime import verify_runtime_validation_directory
from release_supply_chain import (
    DEFAULT_LICENSE_EVIDENCE,
    LICENSE_MANIFEST_FILENAME,
    LICENSE_REVIEW_FILENAME,
    NOTICE_FILENAME,
    SBOM_FILENAME,
    SUPPLY_CHAIN_RECEIPT_FILENAME,
    SupplyChainError,
    generate_supply_chain_materials,
    verify_package_file_manifest,
    write_package_file_manifest,
)
from verify_frozen_setup_archive import verify_archive as verify_updater_archive

from genshin_app import version as app_version
from genshin_app.database_bootstrap import LIVE_MANIFEST_FILENAME
from genshin_app.release_pair_helper import HELPER_RESULT_CONTRACT
from genshin_db.release_hygiene import validate_database_release_hygiene
from genshin_db.release_profile import MANIFEST_FILENAME as DATABASE_MANIFEST_FILENAME
from genshin_db.release_profile import (
    PAIR_MANIFEST_FILENAME,
    DatabaseReleaseError,
    create_database_live_receipt,
    create_database_release_manifest,
    create_release_pair_manifest,
    database_matches_manifest_core,
    load_database_release_manifest,
    load_release_pair_manifest,
    read_database_identity,
    release_pair_matches_database_manifest,
    validate_release_pair_manifest,
    write_database_release_manifest,
    write_release_pair_manifest,
)


def _engine_archive_public_details(error):
    """Expose only archive entry/module identities, never arbitrary error data."""
    details = error.details if isinstance(error.details, dict) else {}
    public = {}
    entry = details.get("entry")
    if isinstance(entry, str) and entry in {"GenshinCalc.exe", "GenshinCalcGUI.exe"}:
        public["entry"] = entry

    def module_name(value):
        return isinstance(value, str) and value.isascii() and all(part.isidentifier() for part in value.split("."))

    if module_name(details.get("module")):
        public["module"] = details["module"]
    modules = details.get("modules")
    if isinstance(modules, list) and all(module_name(name) for name in modules):
        public["modules"] = modules
    return public


@contextmanager
def _managed_build_temp_dir(*, build_root: Path, prefix: str):
    """Create a build-only temporary directory without restrictive ACL modes.

    ``tempfile.TemporaryDirectory`` creates its directory with mode ``0o700``.
    The managed Windows development sandbox maps that mode to an ACL which can
    make the directory inaccessible even to the creating process.  A normal
    ``Path.mkdir`` preserves the workspace ACL while the explicit containment
    check keeps cleanup scoped to ``build-py``.
    """

    build_root.mkdir(parents=True, exist_ok=True)
    canonical_build_root = build_root.resolve(strict=True)
    root = canonical_build_root / f"{prefix}{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        if root.exists():
            canonical_root = root.resolve(strict=True)
            if (
                canonical_root.parent != canonical_build_root
                or not canonical_root.name.startswith(prefix)
            ):
                raise ValueError("unsafe_build_temporary_directory")
            shutil.rmtree(canonical_root)


def _run_packaged_startup_rebind_smoke(
    *,
    package_root: Path,
    database_manifest: dict,
    pair_manifest: dict,
    predecessor_pair: dict,
) -> dict:
    """Exercise the frozen runtime gate with the accepted predecessor pair.

    ``db status`` does not pass through the GUI startup admission and therefore
    cannot detect App/pair migration defects. This smoke uses the built CLI as
    the frozen host, a hard-linked database in an isolated profile, the exact
    accepted predecessor pair supplied to the build, and the same runtime
    command gate used by GUI-backed document operations.
    """

    cli = package_root / "GenshinCalc.exe"
    packaged_database = package_root / "_internal" / "bundled" / "genshin.db"
    if not cli.is_file() or not packaged_database.is_file():
        raise ValueError("packaged_startup_smoke_input_missing")
    predecessor = validate_release_pair_manifest(dict(predecessor_pair))
    pair_sequence = pair_manifest["pair_sequence"]
    if predecessor["pair_sequence"] >= pair_sequence:
        raise ValueError("packaged_startup_smoke_pair_sequence_not_advanced")
    if not release_pair_matches_database_manifest(predecessor, database_manifest):
        raise ValueError("packaged_startup_smoke_predecessor_database_mismatch")
    build_root = ROOT / "build-py"
    with _managed_build_temp_dir(
        build_root=build_root,
        prefix="packaged-startup-rebind-",
    ) as root:
        profile_dir = root / "profile"
        profile_dir.mkdir()
        linked_database = profile_dir / "genshin.db"
        try:
            os.link(packaged_database, linked_database)
        except OSError:
            shutil.copy2(packaged_database, linked_database)
        identity_before = read_database_identity(linked_database)
        write_database_release_manifest(
            profile_dir / "database-live-manifest.json",
            database_manifest,
        )
        receipt = create_database_live_receipt(
            database_manifest,
            identity_before,
            installed_at=database_manifest["released_at"],
        )
        (profile_dir / "database-live-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        live_manifest_path = profile_dir / "database-live-manifest.json"
        live_receipt_path = profile_dir / "database-live-receipt.json"
        manifest_before = live_manifest_path.read_bytes()
        receipt_before = live_receipt_path.read_bytes()
        live_pair_path = profile_dir / PAIR_MANIFEST_FILENAME.replace(
            "release-pair-manifest", "release-pair-live-manifest"
        )
        write_release_pair_manifest(live_pair_path, predecessor)
        output = root / "board.json"
        completed = subprocess.run(
            [
                str(cli),
                "--data-dir",
                str(profile_dir),
                "whiteboard",
                "new",
                "--output",
                str(output),
            ],
            cwd=package_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout).strip()[-2048:]
            raise ValueError(f"packaged_startup_smoke_failed:{detail}")
        rebound_pair = load_release_pair_manifest(live_pair_path)
        identity_after = read_database_identity(linked_database)
        if (
            rebound_pair != pair_manifest
            or identity_after != identity_before
            or live_manifest_path.read_bytes() != manifest_before
            or live_receipt_path.read_bytes() != receipt_before
        ):
            raise ValueError("packaged_startup_smoke_identity_mismatch")
        return {
            "status": "passed",
            "mode": "accepted_predecessor_rebind",
            "prior_pair_sequence": predecessor["pair_sequence"],
            "current_pair_sequence": pair_manifest["pair_sequence"],
            "release_pair_id": rebound_pair["release_pair_id"],
            "database_sha256": identity_after["sha256"],
        }


def _run_packaged_clean_startup_smoke(
    *,
    package_root: Path,
    pair_manifest: dict,
) -> dict:
    """Exercise a new-database package without fabricating an old DB pair."""

    cli = package_root / "GenshinCalc.exe"
    packaged_database = package_root / "_internal" / "bundled" / "genshin.db"
    if not cli.is_file() or not packaged_database.is_file():
        raise ValueError("packaged_clean_startup_smoke_input_missing")
    build_root = ROOT / "build-py"
    with _managed_build_temp_dir(
        build_root=build_root,
        prefix="packaged-clean-startup-",
    ) as root:
        profile_dir = root / "profile"
        profile_dir.mkdir()
        linked_database = profile_dir / "genshin.db"
        try:
            os.link(packaged_database, linked_database)
        except OSError:
            shutil.copy2(packaged_database, linked_database)
        identity_before = read_database_identity(linked_database)
        output = root / "board.json"
        completed = subprocess.run(
            [
                str(cli),
                "--data-dir",
                str(profile_dir),
                "whiteboard",
                "new",
                "--output",
                str(output),
            ],
            cwd=package_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout).strip()[-2048:]
            raise ValueError(f"packaged_clean_startup_smoke_failed:{detail}")
        identity_after = read_database_identity(linked_database)
        if identity_after != identity_before:
            raise ValueError("packaged_clean_startup_smoke_identity_mismatch")
        return {
            "status": "passed",
            "mode": "new_database_clean_startup",
            "current_pair_sequence": pair_manifest["pair_sequence"],
            "release_pair_id": pair_manifest["release_pair_id"],
            "database_sha256": identity_after["sha256"],
        }


def _qualify_packaged_app_startup(
    *, execute_app: bool, package_root: Path, database_manifest: dict,
    pair_manifest: dict, predecessor_pair: dict | None, predecessor_database=None,
) -> dict:
    """Keep static qualification separate from an explicitly permitted App run."""
    if not execute_app:
        return {
            "status": "not_run_user_acceptance",
            "mode": "static_no_app_execution",
            "app_executed": False,
            "prior_pair_sequence": (
                predecessor_pair["pair_sequence"] if predecessor_pair is not None else None
            ),
            "current_pair_sequence": pair_manifest["pair_sequence"],
            "release_pair_id": pair_manifest["release_pair_id"],
        }
    if predecessor_pair is None:
        return _run_packaged_clean_startup_smoke(
            package_root=package_root, pair_manifest=pair_manifest,
        )
    if predecessor_database is not None:
        return run_packaged_database_upgrade_smoke(
            package_root=package_root, database_manifest=database_manifest,
            pair_manifest=pair_manifest, predecessor=predecessor_database,
            temporary_directory=lambda: _managed_build_temp_dir(
                build_root=ROOT / "build-py", prefix="packaged-database-upgrade-"),
        )
    return _run_packaged_startup_rebind_smoke(
        package_root=package_root, database_manifest=database_manifest,
        pair_manifest=pair_manifest, predecessor_pair=predecessor_pair,
    )


def _write_build_qualification(
    *, dist_root: Path, pair_manifest: dict, package_receipt: dict,
    native_receipt: dict, runtime_resource_hashes: dict[str, str], startup: dict,
) -> Path:
    """Write audit evidence outside the sealed App inventory, never a grant."""
    path = dist_root / "build-qualification.json"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    value = {
        "contract": "genshin-portable-build-qualification-v1",
        "schema_version": 1,
        "status": ("built_pending_user_acceptance"
                   if startup["status"] == "not_run_user_acceptance"
                   else "built_and_smoke_verified"),
        "release_pair": pair_manifest,
        "static_checks": {
            "database_artifact": "passed", "release_pair_binding": "passed",
            "package_inventory": package_receipt, "native_runtime": native_receipt,
            "runtime_validation_resource_sha256": runtime_resource_hashes,
        },
        "app_startup": startup,
        "execution_authority": "none",
    }
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                        sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _load_accepted_predecessor_pair(
    path: Path,
    *,
    workspace_root: Path,
    build_root: Path,
    dist_root: Path,
    pair_manifest: dict,
    database_manifest: dict,
    expected_sha256: str,
    allow_database_upgrade: bool = False,
) -> dict:
    """Load the exact reviewed predecessor and reject ambiguous lineage."""

    predecessor_path = path.resolve()
    workspace = workspace_root.resolve()
    if (
        workspace not in predecessor_path.parents
        or predecessor_path.name != PAIR_MANIFEST_FILENAME
        or not predecessor_path.is_file()
        or build_root.resolve() in predecessor_path.parents
        or dist_root.resolve() == predecessor_path
        or dist_root.resolve() in predecessor_path.parents
    ):
        raise DatabaseReleaseError(
            "accepted_predecessor_pair_manifest_path_invalid"
        )
    with predecessor_path.open("rb") as handle:
        actual_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual_sha256 != expected_sha256:
        raise DatabaseReleaseError(
            "accepted_predecessor_pair_manifest_sha256_mismatch"
        )
    predecessor = load_release_pair_manifest(predecessor_path)
    if predecessor["pair_sequence"] != pair_manifest["pair_sequence"] - 1:
        raise DatabaseReleaseError("release_pair_predecessor_not_immediate")
    if (
        predecessor["release_pair_id"] == pair_manifest["release_pair_id"]
        or predecessor["app"]["app_release_id"]
        == pair_manifest["app"]["app_release_id"]
    ):
        raise DatabaseReleaseError("release_pair_successor_identity_not_unique")
    if predecessor["channel"] != pair_manifest["channel"]:
        raise DatabaseReleaseError("release_pair_predecessor_channel_mismatch")
    if predecessor["pair_policy"] != pair_manifest["pair_policy"]:
        raise DatabaseReleaseError("release_pair_predecessor_policy_mismatch")
    if (not release_pair_matches_database_manifest(predecessor, database_manifest)
            and (not allow_database_upgrade
                 or predecessor.get("database", {}).get("database_release_id") == database_manifest.get("database_release_id"))):
        raise DatabaseReleaseError("release_pair_predecessor_database_mismatch")
    return predecessor


def _validate_database_release_hygiene(database: Path, *, workspace_root: Path) -> None:
    validate_database_release_hygiene(database, workspace_root=workspace_root)


def _inno_relative_path(path: Path) -> str:
    relative = str(path.relative_to(ROOT)).replace("/", "\\")
    if any(character in relative for character in ('"', "\r", "\n")):
        raise ValueError("安装器输入路径包含不受支持的字符")
    return relative


def _write_inno_build_inputs(
    *,
    dist_root: Path,
    dist: Path,
    updater_exe: Path,
    updater_receipt: Path,
    updater_sha256: str,
    version: str,
) -> None:
    """Bind Inno Setup to the exact versioned PyInstaller output just built."""

    if (
        len(updater_sha256) != 64
        or any(character not in "0123456789abcdef" for character in updater_sha256)
    ):
        raise ValueError("更新器SHA256不合法")
    updater_root = updater_exe.parent
    if updater_receipt.parent != updater_root or updater_exe.parent == dist:
        raise ValueError("更新器必须位于活动App候选目录之外")
    if dist in updater_root.parents or updater_root in dist.parents:
        raise ValueError("更新器与活动App候选目录不能嵌套")
    if (
        updater_exe.is_symlink()
        or updater_receipt.is_symlink()
        or not updater_exe.is_file()
        or not updater_receipt.is_file()
    ):
        raise ValueError("更新器安装源不完整")
    with updater_exe.open("rb") as handle:
        actual_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    try:
        receipt_value = json.loads(updater_receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("更新器工件收据不合法") from error
    if (
        type(receipt_value) is not dict
        or actual_sha256 != updater_sha256
        or receipt_value.get("contract") != UPDATER_ARTIFACT_CONTRACT
        or receipt_value.get("schema_version") != 1
        or receipt_value.get("build_provenance", {}).get("app", {})
        != {
            "version": app_version.APP_VERSION,
            "release_id": app_version.APP_RELEASE_ID,
            "release_pair_id": app_version.RELEASE_PAIR_ID,
        }
        or receipt_value.get("updater", {}).get("filename")
        != UPDATER_EXE_FILENAME
        or receipt_value.get("updater", {}).get("helper_contract")
        != HELPER_RESULT_CONTRACT
        or receipt_value.get("updater", {}).get("request_schema_version")
        != UPDATER_REQUEST_SCHEMA_VERSION
        or receipt_value.get("updater", {}).get("sha256") != updater_sha256
        or receipt_value.get("updater", {}).get("size_bytes")
        != updater_exe.stat().st_size
    ):
        raise ValueError("更新器工件与收据不一致")
    build_root = ROOT / "build-py"
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root / "app-version.iss").write_text(
        f'#define AppVersion "{version}"\n', encoding="utf-8",
    )
    (build_root / "app-layout.iss").write_text(
        f'#define AppSourceDir "{_inno_relative_path(dist)}"\n'
        f'#define InstallerOutputDir "{_inno_relative_path(dist_root)}"\n'
        f'#define UpdaterSourceFile "{_inno_relative_path(updater_exe)}"\n'
        f'#define UpdaterReceiptFile "{_inno_relative_path(updater_receipt)}"\n'
        f'#define UpdaterSHA256 "{updater_sha256}"\n',
        encoding="utf-8",
    )


def _write_updater_artifact_receipt(updater_exe: Path) -> tuple[Path, dict]:
    """Hash and describe the exact external helper produced by PyInstaller."""

    if (
        updater_exe.name != UPDATER_EXE_FILENAME
        or updater_exe.is_symlink()
        or not updater_exe.is_file()
        or updater_exe.stat().st_size <= 0
    ):
        raise ValueError("更新器构建结果缺失或不是受控普通文件")
    with updater_exe.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    receipt = {
        "contract": UPDATER_ARTIFACT_CONTRACT,
        "schema_version": 1,
        "build_provenance": {
            "app": {
                "version": app_version.APP_VERSION,
                "release_id": app_version.APP_RELEASE_ID,
                "release_pair_id": app_version.RELEASE_PAIR_ID,
            },
        },
        "updater": {
            "filename": UPDATER_EXE_FILENAME,
            "helper_contract": HELPER_RESULT_CONTRACT,
            "request_schema_version": UPDATER_REQUEST_SCHEMA_VERSION,
            "sha256": digest,
            "size_bytes": updater_exe.stat().st_size,
        },
    }
    receipt_path = updater_exe.parent / UPDATER_RECEIPT_FILENAME
    temporary = receipt_path.with_name(f".{receipt_path.name}.tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, receipt_path)
    if json.loads(receipt_path.read_text(encoding="utf-8")) != receipt:
        raise ValueError("更新器工件收据回读不一致")
    return receipt_path, receipt


def _build_updater_artifact(
    *, dist_root: Path, allowed_root: Path
) -> tuple[int, Path | None, Path | None, dict | None]:
    """Build the updater one-file executable in a controlled sibling layout."""

    updater_root = dist_root / UPDATER_ARTIFACT_DIRECTORY
    updater_root.absolute().relative_to(allowed_root.absolute())
    _cleanup_failed_candidate(dist=updater_root, allowed_root=allowed_root)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "genshin_updater.spec",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(updater_root),
        "--workpath",
        "build-py/updater",
    ]
    print("执行:", " ".join(command))
    try:
        result = subprocess.run(command, cwd=ROOT, check=False, env=dict(os.environ))
    except OSError:
        try:
            _cleanup_failed_candidate(dist=updater_root, allowed_root=allowed_root)
        except ValueError:
            pass
        return 1, None, None, None
    if result.returncode != 0:
        try:
            _cleanup_failed_candidate(dist=updater_root, allowed_root=allowed_root)
        except ValueError:
            pass
        return result.returncode, None, None, None
    updater_exe = updater_root / UPDATER_EXE_FILENAME
    try:
        canonical_allowed = allowed_root.resolve(strict=True)
        canonical_updater = updater_root.resolve(strict=True)
        canonical_updater.relative_to(canonical_allowed)
        canonical_exe = updater_exe.resolve(strict=True)
        if (
            not updater_root.is_dir()
            or updater_root.is_symlink()
            or canonical_exe.parent != canonical_updater
            or os.path.normcase(str(updater_root.absolute()))
            != os.path.normcase(str(canonical_updater))
            or os.path.normcase(str(updater_exe.absolute()))
            != os.path.normcase(str(canonical_exe))
        ):
            raise ValueError("更新器构建布局不是受控普通目录")
        verify_updater_archive(updater_exe, ROOT)
        receipt_path, receipt = _write_updater_artifact_receipt(updater_exe)
    except (OSError, ValueError) as error:
        print(f"\n警告：更新器归档未通过校验：{type(error).__name__}")
        try:
            _cleanup_failed_candidate(dist=updater_root, allowed_root=allowed_root)
        except ValueError:
            # An aliased/reparse output is intentionally not followed or
            # deleted.  With Inno inputs already cleared it cannot ship.
            pass
        return 1, None, None, None
    return 0, updater_exe, receipt_path, receipt


def _cleanup_failed_candidate(*, dist: Path, allowed_root: Path) -> None:
    """Remove only the exact failed package child inside the workspace dist."""

    allowed = allowed_root.absolute()
    candidate = dist.absolute()
    candidate.relative_to(allowed)
    if candidate == allowed:
        raise ValueError("不能把dist根目录作为失败候选清理")
    if not candidate.exists():
        return
    canonical_allowed = allowed.resolve(strict=True)
    canonical = candidate.resolve(strict=True)
    canonical.relative_to(canonical_allowed)
    if os.path.normcase(str(candidate)) != os.path.normcase(str(canonical)):
        raise ValueError("失败候选不能是目录别名")
    shutil.rmtree(candidate)


def _clear_inno_build_inputs() -> None:
    for name in ("app-version.iss", "app-layout.iss"):
        target = ROOT / "build-py" / name
        try:
            target.unlink(missing_ok=True)
        except OSError as error:
            raise ValueError("无法清理旧安装器构建输入") from error


def _record_native_release_failure(error: NativeAgentReleaseError, *, phase: str) -> None:
    """Best-effort path-free failure evidence; never changes the rejection."""
    diagnostic = getattr(error, "diagnostic", None)
    if not isinstance(diagnostic, dict):
        return
    try:
        destination = ROOT / "build" / "native-agent-v2" / f"failure-{uuid.uuid4().hex}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump({"phase": phase, **diagnostic}, stream, ensure_ascii=False, indent=2,
                      allow_nan=False)
            stream.write("\n")
        print("原生Agent失败诊断:", destination.relative_to(ROOT).as_posix())
    except (OSError, TypeError, ValueError):
        # A diagnostic is neither an acceptance receipt nor a second outcome.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="构建原神拉表便携版")
    parser.add_argument("--dist-root", default="dist", help="工作区内的产物父目录；可保留旧版并行构建")
    parser.add_argument("--database", default="data/genshin.db", help="工作区内待打包的 genshin.db；允许先验收候选库再发布")
    parser.add_argument(
        "--accepted-database-manifest",
        default=None,
        help=(
            "复用未变化数据库发行时显式提供既有accepted manifest；"
            "构建器将保留其原始DB身份/指纹并只生成新ReleasePair"
        ),
    )
    parser.add_argument(
        "--new-database-release",
        action="store_true",
        help=(
            "仅在本次确实发布新的数据库发行时使用；与"
            "--accepted-database-manifest互斥，防止App单独升级时"
            "为既有DB身份静默重签manifest"
        ),
    )
    parser.add_argument(
        "--native-node",
        default=None,
        help="明确指定Node 24.19.x的node.exe；省略时只读取GENSHIN_NATIVE_NODE",
    )
    parser.add_argument(
        "--native-plan-only",
        action="store_true",
        help="仅离线验证并写build-py/native-agent-build-plan.json，不执行PyInstaller",
    )
    parser.add_argument(
        "--no-app-execution", action="store_true",
        help=("构建和静态门禁照常执行，但不启动任何App EXE；"
              "启动验收明确记录为not_run_user_acceptance。Node依赖校验仍执行"),
    )
    parser.add_argument(
        "--node-license",
        default=None,
        help="精确Node归档随附的LICENSE路径；缺省时使用已核验的许可材料（如有）",
    )
    parser.add_argument(
        "--python-license",
        default=None,
        help="本次Python运行时LICENSE路径；缺省时读取sys.base_prefix/LICENSE.txt",
    )
    parser.add_argument(
        "--license-evidence",
        default=str(DEFAULT_LICENSE_EVIDENCE) if DEFAULT_LICENSE_EVIDENCE.is_file() else None,
        help="离线许可核查材料；绑定精确依赖版本、元数据及许可证摘要，更新依赖后需重新核对",
    )
    parser.add_argument(
        "--database-release-id",
        default=app_version.DATABASE_RELEASE_ID,
        help="本次受审数据库发行号；独立于上游commit和展示游戏版本",
    )
    parser.add_argument(
        "--database-release-sequence",
        type=int,
        default=app_version.DATABASE_RELEASE_SEQUENCE,
        help="同一数据库发行渠道内单调递增的正整数序号",
    )
    parser.add_argument(
        "--database-channel",
        choices=("stable", "dev"),
        default=app_version.DATABASE_RELEASE_CHANNEL,
    )
    parser.add_argument(
        "--release-pair-id",
        default=app_version.RELEASE_PAIR_ID,
        help="精确绑定本次App与数据库工件的版本对ID",
    )
    parser.add_argument(
        "--release-pair-sequence",
        type=int,
        default=app_version.RELEASE_PAIR_SEQUENCE,
        help="版本对渠道内单调递增的防回滚序号",
    )
    parser.add_argument(
        "--accepted-predecessor-pair-manifest",
        default=None,
        help=(
            "最近已接受便携候选的release-pair-manifest.json；完整构建必填，"
            "用于拒绝同序号分叉并驱动冻结CLI真实前代换代冒烟"
        ),
    )
    parser.add_argument("--accepted-predecessor-database", default=None,
        help="新DB升级验证的前代SQLite；缺省仅取受审前代Pair同目录的genshin.db")
    parser.add_argument("--accepted-predecessor-database-manifest", default=None,
        help="前代DB发行清单；缺省仅取受审前代Pair同目录的database-release-manifest.json")
    parser.add_argument(
        "--pair-policy",
        choices=("exact", "current_only"),
        default=app_version.DATABASE_PAIR_POLICY,
        help="正式发行使用exact；开发工作树只使用current_only",
    )
    parser.add_argument(
        "--released-at",
        default=None,
        help=(
            "本次App/Pair发行时间；复用既有DB时不改DB发行时间。"
            "新建stable DB时必填；省略时Pair沿用DB发行时间，"
            "新建dev DB沿用上游published_at"
        ),
    )
    args = parser.parse_args()
    if (args.accepted_database_manifest is None) == (not args.new_database_release):
        parser.error(
            "必须且只能选择一种数据库发行来源：复用既有发行时提供"
            "--accepted-database-manifest；真实新数据库发行时显式提供"
            "--new-database-release"
        )
    if (
        not args.native_plan_only
        and args.release_pair_sequence > 1
        and args.accepted_predecessor_pair_manifest is None
    ):
        parser.error(
            "已有前代的完整构建必须提供"
            "--accepted-predecessor-pair-manifest"
        )
    release_pins = {
        "database_release_id": app_version.DATABASE_RELEASE_ID,
        "database_release_sequence": app_version.DATABASE_RELEASE_SEQUENCE,
        "database_channel": app_version.DATABASE_RELEASE_CHANNEL,
        "release_pair_id": app_version.RELEASE_PAIR_ID,
        "release_pair_sequence": app_version.RELEASE_PAIR_SEQUENCE,
        "pair_policy": app_version.DATABASE_PAIR_POLICY,
    }
    mismatched_pins = [
        name for name, expected in release_pins.items()
        if getattr(args, name) != expected
    ]
    if mismatched_pins:
        parser.error(
            "发行身份必须由version.py统一固定，不能通过CLI覆盖："
            + ",".join(mismatched_pins)
        )
    dist_root = (ROOT / args.dist_root).resolve()
    # PyInstaller 会清理目标目录，必须先验证最终路径属于本工作区的 dist。
    allowed_root = (ROOT / "dist").resolve()
    if dist_root != allowed_root and allowed_root not in dist_root.parents:
        parser.error("产物目录必须在当前工作区的 dist 内")
    dist = dist_root / "GenshinCalc"
    updater_dist = dist_root / UPDATER_ARTIFACT_DIRECTORY
    database = (ROOT / args.database).resolve()
    if ROOT not in database.parents or database.name != "genshin.db" or not database.is_file():
        parser.error("预置数据库必须是当前工作区内已存在的 genshin.db 文件")
    for cleaned in (
        dist.resolve(),
        updater_dist.resolve(),
        (ROOT / "build-py").resolve(),
    ):
        if cleaned == database or cleaned in database.parents:
            parser.error("预置数据库不能位于本次构建会清理的输出或工作目录")
    try:
        _validate_database_release_hygiene(database, workspace_root=ROOT)
    except ValueError as error:
        parser.error(f"预置数据库发行清洁校验失败：{error}")
    database_manifest_path = ROOT / "build-py" / DATABASE_MANIFEST_FILENAME
    pair_manifest_path = ROOT / "build-py" / PAIR_MANIFEST_FILENAME
    predecessor_pair: dict | None = None
    predecessor_database = None
    accepted_manifest_bytes = None
    try:
        if args.accepted_database_manifest is not None:
            accepted_path = (ROOT / args.accepted_database_manifest).resolve()
            if (
                ROOT not in accepted_path.parents
                or accepted_path.name not in (DATABASE_MANIFEST_FILENAME, LIVE_MANIFEST_FILENAME)
                or not accepted_path.is_file()
                or dist_root == accepted_path
                or dist_root in accepted_path.parents
                or (ROOT / "build-py").resolve() in accepted_path.parents
            ):
                raise DatabaseReleaseError(
                    "accepted_database_manifest_path_invalid"
                )
            database_manifest = load_database_release_manifest(accepted_path)
            accepted_manifest_bytes = accepted_path.read_bytes()
            expected_contract = {
                "database_release_id": args.database_release_id,
                "release_sequence": args.database_release_sequence,
                "channel": args.database_channel,
                "logic_profile": app_version.DATABASE_LOGIC_PROFILE,
                "data_contract_version": app_version.DATABASE_DATA_CONTRACT_VERSION,
            }
            actual_contract = {
                field: database_manifest.get(field) for field in expected_contract
            }
            if actual_contract != expected_contract:
                raise DatabaseReleaseError(
                    "accepted_database_manifest_contract_mismatch"
                )
            identity = read_database_identity(database)
            accepted_database = database_manifest["database"]
            if (
                identity.get("sha256") != accepted_database.get("sha256")
                or identity.get("size_bytes") != accepted_database.get("size_bytes")
                or identity.get("meta_fingerprint")
                != accepted_database.get("meta_fingerprint")
                or not database_matches_manifest_core(identity, accepted_database)
            ):
                raise DatabaseReleaseError(
                    "accepted_database_manifest_artifact_mismatch"
                )
        else:
            database_manifest = create_database_release_manifest(
                database,
                database_release_id=args.database_release_id,
                release_sequence=args.database_release_sequence,
                channel=args.database_channel,
                release_pair_id=args.release_pair_id,
                app_release_id=app_version.APP_RELEASE_ID,
                released_at=args.released_at,
                logic_profile=app_version.DATABASE_LOGIC_PROFILE,
                data_contract_version=app_version.DATABASE_DATA_CONTRACT_VERSION,
            )
        pair_manifest = create_release_pair_manifest(
            database_manifest,
            release_pair_id=args.release_pair_id,
            pair_sequence=args.release_pair_sequence,
            pair_policy=args.pair_policy,
            app_release_id=app_version.APP_RELEASE_ID,
            app_version=app_version.APP_VERSION,
            released_at=args.released_at,
            # PyInstaller needs the descriptor path while assembling the
            # package.  The detached descriptor is replaced and independently
            # revalidated below after the actual package inventory exists.
            app_artifact_manifest_sha256="0" * 64,
        )
        if args.accepted_predecessor_pair_manifest is not None:
            predecessor_pair = _load_accepted_predecessor_pair(
                ROOT / args.accepted_predecessor_pair_manifest,
                workspace_root=ROOT,
                build_root=ROOT / "build-py",
                dist_root=dist_root,
                pair_manifest=pair_manifest,
                database_manifest=database_manifest,
                expected_sha256=(
                    app_version.ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256
                ),
                allow_database_upgrade=True,
            )
            if not release_pair_matches_database_manifest(predecessor_pair, database_manifest):
                predecessor_database = load_predecessor_database(
                    pair_path=ROOT / args.accepted_predecessor_pair_manifest,
                    predecessor_pair=predecessor_pair, target_manifest=database_manifest,
                    workspace_root=ROOT, build_root=ROOT / "build-py", dist_root=dist_root,
                    expected_pair_sha256=app_version.ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256,
                    database_path=ROOT / args.accepted_predecessor_database if args.accepted_predecessor_database else None,
                    manifest_path=ROOT / args.accepted_predecessor_database_manifest if args.accepted_predecessor_database_manifest else None,
                )
        if accepted_manifest_bytes is None:
            write_database_release_manifest(database_manifest_path, database_manifest)
        else:
            database_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            database_manifest_path.write_bytes(accepted_manifest_bytes)
        write_release_pair_manifest(pair_manifest_path, pair_manifest)
        loaded_database_manifest = load_database_release_manifest(database_manifest_path)
        loaded_pair_manifest = load_release_pair_manifest(pair_manifest_path)
        if (
            loaded_database_manifest != database_manifest
            or loaded_pair_manifest != pair_manifest
        ):
            raise DatabaseReleaseError("database_release_manifest_roundtrip_mismatch")
        if (
            not args.native_plan_only
            and args.release_pair_sequence > 1
            and predecessor_pair is None
        ):
            raise DatabaseReleaseError(
                "accepted_predecessor_pair_manifest_required"
            )
    except DatabaseReleaseError as error:
        parser.error(f"预置数据库发行清单校验失败：{error.reason}")

    try:
        native_plan = create_native_agent_build_plan(
            project_root=ROOT,
            native_node=args.native_node,
            environment=os.environ,
        )
    except NativeAgentReleaseError as error:
        detail = f"/{error.cause_code}" if error.cause_code else ""
        parser.error(f"原生Agent发行输入验证失败：{error.code}{detail}")
    plan_receipt = ROOT / "build-py" / "native-agent-build-plan.json"
    if args.native_plan_only:
        write_build_plan(plan_receipt, native_plan)
        pair_manifest_path.unlink(missing_ok=True)
        print(json.dumps(native_plan.public(), ensure_ascii=False, sort_keys=True))
        return 0
    if not native_plan.release_ready:
        parser.error(
            "原生Agent发行计划存在阻断："
            + ",".join(native_plan.release_blockers),
        )
    # A previous attempt's success evidence must not survive a failed rebuild.
    qualification_path = dist_root / "build-qualification.json"
    qualification_path.unlink(missing_ok=True)
    _clear_inno_build_inputs()
    try:
        # Never allow an updater left by an older build to be paired with the
        # App candidate produced by this invocation.
        _cleanup_failed_candidate(dist=updater_dist, allowed_root=allowed_root)
    except ValueError as error:
        parser.error(f"旧更新器候选无法安全清理：{error}")

    staging_parent = ROOT / "build" / "native-agent-v2"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = staging_parent / "pyinstaller-stage"
    try:
        prepared_stage = prepare_native_agent_stage(
            native_plan,
            staging_root=staging_root,
            allowed_parent=staging_parent,
        )
    except NativeAgentReleaseError as error:
        _record_native_release_failure(error, phase="stage")
        try:
            cleanup_native_agent_stage(
                staging_root,
                allowed_parent=staging_parent,
            )
        except NativeAgentReleaseError as cleanup_error:
            parser.error(
                "原生Agent发行staging失败且清理失败："
                f"{error.code}/{cleanup_error.code}",
            )
        detail = f"/{error.cause_code}" if error.cause_code else ""
        parser.error(f"原生Agent发行staging失败：{error.code}{detail}")

    supply_chain_directory = (
        prepared_stage / "native-agent-release" / "supply-chain"
    )
    try:
        supply_chain_receipt = generate_supply_chain_materials(
            output_directory=supply_chain_directory,
            app_version=native_plan.product_app_version,
            runtime_root=native_plan.runtime_root,
            node_executable=native_plan.node_executable,
            node_version=native_plan.identity["node_version"],
            node_license_path=(Path(args.node_license) if args.node_license else None),
            license_evidence_path=Path(args.license_evidence) if args.license_evidence else None,
            python_license_path=(
                Path(args.python_license) if args.python_license else None
            ),
            database=database,
            pnpm_lock=native_plan.runtime_root / "pnpm-lock.yaml",
            runtime_manifest=native_plan.manifest_path,
        )
    except SupplyChainError as error:
        try:
            cleanup_native_agent_stage(
                staging_root,
                allowed_parent=staging_parent,
            )
        except NativeAgentReleaseError as cleanup_error:
            parser.error(
                "供应链材料生成失败且staging清理失败："
                f"{error.code}/{cleanup_error.code}",
            )
        parser.error(f"供应链材料生成失败：{error.code}")

    cmd = [sys.executable, "-m", "PyInstaller", "genshin_app.spec",
           "--clean", "--noconfirm", "--distpath", str(dist_root), "--workpath", "build-py"]
    print("执行:", " ".join(cmd))
    environment = dict(
        os.environ,
        GENSHIN_BUNDLED_DATABASE=str(database),
        GENSHIN_DATABASE_RELEASE_MANIFEST=str(database_manifest_path),
        GENSHIN_RELEASE_PAIR_MANIFEST=str(pair_manifest_path),
        GENSHIN_NATIVE_AGENT_STAGE=str(prepared_stage),
    )
    try:
        r = subprocess.run(cmd, cwd=ROOT, check=False, env=environment)
        if r.returncode == 0:
            preserve_packaged_native_dependency_directories(
                staging_root=prepared_stage, package_root=dist,
                expected_directories=native_plan.dependencies["directories"],
            )
    except NativeAgentReleaseError as error:
        _record_native_release_failure(error, phase="directory_publication")
        print(f"\n警告：原生Agent目录发布失败：{error.code}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    finally:
        try:
            cleanup_native_agent_stage(
                staging_root,
                allowed_parent=staging_parent,
            )
        except NativeAgentReleaseError as error:
            parser.error(f"原生Agent发行staging清理失败：{error.code}")
    write_build_plan(plan_receipt, native_plan)
    if r.returncode != 0:
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return r.returncode

    exe = dist / "GenshinCalc.exe"
    gui_exe = dist / "GenshinCalcGUI.exe"
    if not exe.exists() or not gui_exe.exists():
        print("\n警告：构建结果缺少一个或多个正式入口")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    try:
        runtime_resource_hashes = verify_runtime_validation_directory(dist / "_internal", ROOT)
    except (OSError, ValueError) as error:
        reason = getattr(error, "reason", type(error).__name__)
        print(f"\n警告：最终包数据库校验资源不完整：{reason}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    try:
        engine_archive_receipt = validate_engine_package_archive(dist)
    except EngineRuntimePackageError as error:
        print(f"\n警告：最终包引擎动态依赖资格失败：{error.reason}")
        details = _engine_archive_public_details(error)
        if details:
            print("归档检查详情：" + json.dumps(details, ensure_ascii=False, sort_keys=True))
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    engine_receipt_path = ROOT / "build" / "engine-runtime-archive-qualification.json"
    engine_receipt_path.write_text(json.dumps(engine_archive_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    packaged_database = dist / "_internal" / "bundled" / "genshin.db"
    packaged_database_manifest = (
        dist / "_internal" / "bundled" / DATABASE_MANIFEST_FILENAME
    )
    packaged_pair_manifest = (
        dist / "_internal" / "bundled" / PAIR_MANIFEST_FILENAME
    )
    try:
        packaged_manifest = load_database_release_manifest(packaged_database_manifest)
        packaged_pair = load_release_pair_manifest(packaged_pair_manifest)
        packaged_identity = read_database_identity(packaged_database)
        if (
            packaged_manifest != database_manifest
            or (accepted_manifest_bytes is not None and packaged_database_manifest.read_bytes() != accepted_manifest_bytes)
            or packaged_pair != pair_manifest
            or packaged_manifest["database"]["sha256"] != packaged_identity["sha256"]
            or packaged_manifest["database"]["meta_fingerprint"]
            != packaged_identity["meta_fingerprint"]
        ):
            raise DatabaseReleaseError("database_release_package_identity_mismatch")
    except DatabaseReleaseError as error:
        print(f"\n警告：最终包数据库发行门禁失败：{error.reason}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    try:
        native_package_receipt = validate_packaged_native_agent_runtime(
            native_plan,
            package_root=dist,
            environment=os.environ,
        )
    except NativeAgentReleaseError as error:
        _record_native_release_failure(error, phase="packaged_runtime")
        detail = f"/{error.cause_code}" if error.cause_code else ""
        print(
            "\n警告：最终包原生Agent门禁失败："
            f"{error.code}{detail}",
        )
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1

    # skill/ 已在 datas 进 _internal；再复制一份到包根，方便用户直接取用（R6 Skill 捆绑）
    dst_skill = dist / "skill"
    if dst_skill.exists():
        shutil.rmtree(dst_skill)
    shutil.copytree(ROOT / "skill", dst_skill)

    packaged_supply_chain = (
        dist / "_internal" / "native-agent-release" / "supply-chain"
    )
    if not packaged_supply_chain.is_dir():
        print("\n警告：构建结果缺少供应链材料")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    for name in (
        SBOM_FILENAME,
        LICENSE_MANIFEST_FILENAME,
        LICENSE_REVIEW_FILENAME,
        NOTICE_FILENAME,
        SUPPLY_CHAIN_RECEIPT_FILENAME,
    ):
        source = packaged_supply_chain / name
        if not source.is_file():
            print(f"\n警告：构建结果缺少供应链材料 {name}")
            _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
            return 1
        shutil.copy2(source, dist / name)
    try:
        package_receipt = write_package_file_manifest(dist)
    except SupplyChainError as error:
        print(f"\n警告：最终包文件清单生成失败：{error.code}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    try:
        pair_manifest = create_release_pair_manifest(
            database_manifest,
            release_pair_id=args.release_pair_id,
            pair_sequence=args.release_pair_sequence,
            pair_policy=args.pair_policy,
            app_release_id=app_version.APP_RELEASE_ID,
            app_version=app_version.APP_VERSION,
            app_artifact_manifest_sha256=package_receipt["manifest_sha256"],
            released_at=args.released_at,
        )
        write_release_pair_manifest(pair_manifest_path, pair_manifest)
        write_release_pair_manifest(packaged_pair_manifest, pair_manifest)
        packaged_pair = load_release_pair_manifest(packaged_pair_manifest)
        verified_package = verify_package_file_manifest(dist)
        if (
            packaged_pair != pair_manifest
            or not release_pair_matches_database_manifest(
                packaged_pair, database_manifest
            )
            or packaged_pair["app"]["artifact_manifest"]["sha256"]
            != verified_package["manifest_sha256"]
            or verified_package["manifest_sha256"]
            != package_receipt["manifest_sha256"]
        ):
            raise DatabaseReleaseError("release_pair_app_artifact_mismatch")
    except (DatabaseReleaseError, SupplyChainError) as error:
        reason = getattr(error, "reason", getattr(error, "code", type(error).__name__))
        print(f"\n警告：最终App/数据库版本对门禁失败：{reason}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1

    try:
        startup_smoke = _qualify_packaged_app_startup(
            execute_app=not args.no_app_execution, package_root=dist,
            database_manifest=database_manifest, pair_manifest=pair_manifest,
            predecessor_pair=predecessor_pair,
            predecessor_database=predecessor_database,
        )
    except (DatabaseReleaseError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"\n警告：最终包真实启动／旧App配对换代冒烟失败：{error}")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1

    updater_code, updater_exe, updater_receipt_path, updater_receipt = (
        _build_updater_artifact(
            dist_root=dist_root,
            allowed_root=allowed_root,
        )
    )
    if (
        updater_code != 0
        or updater_exe is None
        or updater_receipt_path is None
        or updater_receipt is None
    ):
        print("\n警告：独立更新器构建或完整性门禁失败")
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return updater_code or 1

    # 只有完整候选和包根Skill都就绪后才发布Inno输入；失败构建不能留下
    # 指向半成品目录的新安装器配置。
    version = runpy.run_path(str(ROOT / "src" / "genshin_app" / "version.py"))["APP_VERSION"]
    try:
        _write_inno_build_inputs(
            dist_root=dist_root,
            dist=dist,
            updater_exe=updater_exe,
            updater_receipt=updater_receipt_path,
            updater_sha256=updater_receipt["updater"]["sha256"],
            version=version,
        )
    except (OSError, TypeError, ValueError, KeyError):
        print("\n警告：安装器受控布局生成失败")
        _clear_inno_build_inputs()
        _cleanup_failed_candidate(dist=updater_dist, allowed_root=allowed_root)
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    try:
        qualification_path = _write_build_qualification(
            dist_root=dist_root, pair_manifest=pair_manifest,
            package_receipt=verified_package, native_receipt=native_package_receipt,
            runtime_resource_hashes=runtime_resource_hashes, startup=startup_smoke,
        )
    except (OSError, TypeError, ValueError):
        print("\n警告：构建资格收据写入失败；候选未发布")
        _clear_inno_build_inputs()
        _cleanup_failed_candidate(dist=updater_dist, allowed_root=allowed_root)
        _cleanup_failed_candidate(dist=dist, allowed_root=allowed_root)
        return 1
    print(
        "供应链材料："
        f"{supply_chain_receipt['component_counts']['total']} components，"
        f"{supply_chain_receipt['review_required_count']} review items",
    )
    print(
        "最终包清单："
        f"{package_receipt['files']} files / {package_receipt['bytes']} bytes / "
        f"{package_receipt['manifest_sha256']}",
    )
    print(
        "独立更新器："
        f"{updater_receipt['updater']['size_bytes']} bytes / "
        f"{updater_receipt['updater']['sha256']}",
    )
    if startup_smoke["status"] == "not_run_user_acceptance":
        print("App启动验收：not_run_user_acceptance（本次未启动，交由用户验收）")
    elif startup_smoke.get("prior_pair_sequence") is None:
        print(f"真实首次启动冒烟：{startup_smoke['status']} / pair {startup_smoke['current_pair_sequence']}")
    else:
        print("真实启动换代冒烟："
              f"{startup_smoke['status']} / "
              f"pair {startup_smoke['prior_pair_sequence']} -> "
              f"{startup_smoke['current_pair_sequence']}")
    print(f"构建资格收据：{qualification_path}")
    print(f"\n构建完成: {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
