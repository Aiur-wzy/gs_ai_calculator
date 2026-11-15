"""Offline release planning and staging for the App-native Agent runtime.

The builder never discovers Node through PATH and never invokes a package
manager.  It validates one explicitly supplied Node executable against the
independent pins compiled into :mod:`genshin_app.version`, verifies the exact
DSH client-shell runtime and frontend roots, and stages the frozen production graph.

DSH's current package graph uses peer-specific resolution and runtime plugins.
Removing individual transitive packages without a new upstream composition
audit is unsafe, so the first release keeps the complete locked production
closure.  That closure must be prepared ahead of the build with the pinned
pnpm in physical ``node-linker=hoisted`` form; the builder never installs it.
Only project-owned client-shell files are selected from the repository; probes and
files for older phases are excluded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_app.agent_chat.native_dsh_dependency_payloads import (
    DEPENDENCY_METADATA_POLICY,
    INSTALL_ONLY_MODULE_FIELDS,
    DependencyPayloadError,
    project_dependency_shim,
)
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_production import verify_native_dsh_production
from genshin_app.agent_chat.runtime_integrity import RuntimeIntegrityError
from genshin_app.version import (
    APP_VERSION,
    NATIVE_AGENT_DEPENDENCY_MODE,
    NATIVE_AGENT_DSH_RELEASE,
    NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
    NATIVE_AGENT_NODE_BINARY_SHA256,
    NATIVE_AGENT_NODE_VERSION,
    NATIVE_AGENT_PACKAGE_MANAGER,
)

CONTROLLED_NODE_ENV = "GENSHIN_NATIVE_NODE"
RUNTIME_DIRECTORY = "agent-runtime"
ASSETS_DIRECTORY = "agent-client-assets"
RUNTIME_MANIFEST = "dsh-client-shell-runtime-manifest.json"
RELEASE_IDENTITY_DIRECTORY = "native-agent-release"
RELEASE_IDENTITY_FILENAME = "release-identity.json"
BUILD_PLAN_FILENAME = "native-agent-build-plan.json"

_NODE_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)\Z")
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_STAGE_RELATIVE_FILE = re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._@+/-]+\Z")
_PORTABLE_NODE_LINKER = "hoisted"
_MODULES_STATE_FILENAME = ".modules.yaml"
_WORKSPACE_STATE_FILENAME = ".pnpm-workspace-state-v1.json"
_WINDOWS_HOST_PATH = re.compile(
    r"(?i)(?<![a-z0-9_])(?:[a-z]:[\\/]|\\\\(?:\?\\)?[^\\/\s]+[\\/][^\\/\s]+)",
)
_NO_ROOT_EXPORT_DEPENDENCIES = frozenset({"@deepseek-ai/dsh"})
_PRODUCTION_IMPORTS = (
    "./src/native-dsh-runtime.mjs",
    "./src/native-dsh-session-host.mjs",
    "./src/native-dsh-client-assets.mjs",
    "./src/native-dsh-settings.mjs",
)


class NativeAgentReleaseError(RuntimeError):
    """Path-free release preparation failure with a stable code."""

    def __init__(self, code: str, *, cause_code: str | None = None):
        super().__init__(code)
        self.code = code
        self.cause_code = cause_code


class NativeDependencyLayoutError(NativeAgentReleaseError):
    """A release rejection with path-free, non-authoritative failure evidence."""

    def __init__(self, code: str, *, expected: Mapping, actual: Mapping):
        super().__init__(code)
        fields = ("mode", "direct_dependencies", "package_manager", "node_linker",
                  "metadata_normalization", "reparse_points", "files", "directories",
                  "links", "bytes", "tree_sha256", "direct_resolutions", "production_imports")
        self.diagnostic = {
            "contract": "genshin-native-dependency-layout-failure-v1",
            "code": code,
            "expected": {key: expected.get(key) for key in fields},
            "actual": {key: actual.get(key) for key in fields},
            "different_fields": [key for key in fields if expected.get(key) != actual.get(key)],
            "qualification": False,
        }


def _fail(code: str, *, cause_code: str | None = None) -> None:
    raise NativeAgentReleaseError(code, cause_code=cause_code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        _fail("native_release_file_read")
    return digest.hexdigest()


def release_identity() -> dict[str, Any]:
    """Return the independent trust anchor shipped outside runtime files."""

    return {
        "contract": "genshin-native-agent-release-identity-v2",
        "runtime_generation": "dsh_client_shell",
        "phase": "dsh_client_shell",
        "manifest_sha256": NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
        "node_version": NATIVE_AGENT_NODE_VERSION,
        "node_binary_sha256": NATIVE_AGENT_NODE_BINARY_SHA256,
        "dsh_release": NATIVE_AGENT_DSH_RELEASE.removeprefix("dsh-v"),
        "package_manager": NATIVE_AGENT_PACKAGE_MANAGER,
        "dependency_mode": NATIVE_AGENT_DEPENDENCY_MODE,
    }


def _load_dsh_client_shell_manifest(
    manifest_path: Path, *, expected_sha256: str,
    runtime_root: Path, assets_root: Path, node_executable: Path,
) -> dict[str, Any]:
    """Use the same dual-root production verifier as the real GUI launcher."""
    return verify_native_dsh_production(
        manifest_path=manifest_path, expected_manifest_sha256=expected_sha256,
        runtime_root=runtime_root, assets_root=assets_root,
        node_executable=node_executable, verification_mode="release",
    )


def resolve_explicit_node(
    cli_value: str | os.PathLike[str] | None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve only CLI or the one controlled build environment variable."""

    variables = os.environ if environment is None else environment
    raw = cli_value if cli_value not in (None, "") else variables.get(CONTROLLED_NODE_ENV)
    if raw in (None, ""):
        _fail("native_release_node_required")
    try:
        declared = Path(raw).absolute()
        if declared.is_symlink() or not declared.is_file():
            _fail("native_release_node_missing")
        canonical = declared.resolve(strict=True)
    except NativeAgentReleaseError:
        raise
    except (OSError, TypeError, ValueError):
        _fail("native_release_node_missing")
    if os.path.normcase(str(declared)) != os.path.normcase(str(canonical)):
        _fail("native_release_node_alias")
    return canonical


def _read_node_version(
    node: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    # ``NODE_OPTIONS=--require/--import`` executes code even for ``--version``.
    # The release validator therefore gives the explicitly addressed binary
    # only the small Windows process environment it needs, never PATH or any
    # Node startup hook inherited from the developer shell.
    source = os.environ if environment is None else environment
    child_environment = {
        name: source[name]
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")
        if source.get(name)
    }
    try:
        result = subprocess.run(
            [str(node), "--version"],
            env=child_environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        _fail("native_release_node_version_unavailable")
    value = result.stdout.strip()
    if result.returncode != 0 or result.stderr.strip() or _NODE_VERSION.fullmatch(value) is None:
        _fail("native_release_node_version_unavailable")
    return value.lstrip("v")


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_TEXT_BYTES:
            _fail(code)
        value = json.loads(path.read_text(encoding="utf-8"))
    except NativeAgentReleaseError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def _inside(root: Path, target: Path, code: str) -> None:
    try:
        target.relative_to(root)
    except ValueError:
        _fail(code)


def _is_reparse_point(path: Path) -> bool:
    """Return whether *path* is any aliasing filesystem object on Windows."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        attributes = getattr(
            path.stat(follow_symlinks=False), "st_file_attributes", 0,
        )
    except OSError:
        _fail("native_release_dependency_tree_unreadable")
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _validate_link_graph(
    node_modules: Path,
    *,
    projected_files: Mapping[str, bytes] | None = None,
    excluded_files: frozenset[str] = frozenset(),
    require_canonical_shims: bool = False,
) -> dict[str, int]:
    """Audit the portable dependency tree and reject every reparse point.

    PyInstaller's data collector drops pnpm directory symbolic links on
    Windows.  A release input must therefore already be the physical,
    ``node-linker=hoisted`` production layout; accepting a correct isolated
    source graph here would create a corrupt portable package later.
    """

    projections = {} if projected_files is None else dict(projected_files)
    file_count = 0
    directory_count = 0
    link_count = 0
    byte_count = 0
    file_rows: list[tuple[str, str]] = []
    file_names: set[str] = set()
    pending = [node_modules]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            _fail("native_release_dependency_tree_unreadable")
        for entry in entries:
            path = Path(entry.path)
            try:
                relative = path.relative_to(node_modules).as_posix()
                if _is_reparse_point(path):
                    _fail("native_release_dependency_reparse_unsupported")
                if entry.is_dir(follow_symlinks=False):
                    directory_count += 1
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    if relative in excluded_files:
                        continue
                    release_name, shim_bytes = project_dependency_shim(relative, path)
                    if require_canonical_shims and (release_name != relative or shim_bytes is not None):
                        _fail("native_release_dependency_shim_noncanonical")
                    if release_name in file_names:
                        _fail("native_release_dependency_shim_name_collision")
                    file_names.add(release_name)
                    file_count += 1
                    projected = projections.get(relative, shim_bytes)
                    if projected is None:
                        byte_count += entry.stat(follow_symlinks=False).st_size
                        digest = _sha256_file(path)
                    else:
                        byte_count += len(projected)
                        digest = hashlib.sha256(projected).hexdigest()
                    file_rows.append((release_name, digest))
                else:
                    _fail("native_release_dependency_special_file")
            except NativeAgentReleaseError:
                raise
            except DependencyPayloadError as error:
                _fail(error.code)
            except (OSError, ValueError):
                _fail("native_release_dependency_tree_unreadable")
    tree_digest = hashlib.sha256()
    for relative, digest in sorted(file_rows):
        tree_digest.update(relative.encode("utf-8"))
        tree_digest.update(b"\0")
        tree_digest.update(digest.encode("ascii"))
        tree_digest.update(b"\n")
    return {
        "files": file_count,
        "directories": directory_count,
        "links": link_count,
        "bytes": byte_count,
        "tree_sha256": tree_digest.hexdigest(),
    }


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("native_release_dependency_state_invalid")


def _reject_windows_host_paths(value: Any) -> None:
    """Reject build-host paths from every key and value in release metadata."""

    if isinstance(value, str):
        if _WINDOWS_HOST_PATH.search(value) is not None:
            _fail("native_release_dependency_metadata_host_path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_windows_host_paths(key)
            _reject_windows_host_paths(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_windows_host_paths(item)


def _read_dependency_release_metadata(
    node_modules: Path,
    *,
    require_canonical_release: bool,
) -> bytes:
    """Validate pnpm state and return its portable, canonical projection.

    The source installation is deliberately read-only and may contain pnpm's
    install-only fields. Staged and packaged trees must contain only this
    canonical projection and must not contain pnpm's workspace validation
    cache.
    """

    modules_state = node_modules / _MODULES_STATE_FILENAME
    try:
        if (
            not modules_state.is_file()
            or modules_state.is_symlink()
            or modules_state.stat().st_size > _MAX_TEXT_BYTES
        ):
            _fail("native_release_dependency_state_missing")
        raw = modules_state.read_bytes()
        state = json.loads(raw.decode("utf-8"))
    except NativeAgentReleaseError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("native_release_dependency_state_invalid")
    if not isinstance(state, dict):
        _fail("native_release_dependency_state_invalid")
    if state.get("packageManager") != NATIVE_AGENT_PACKAGE_MANAGER:
        _fail("native_release_package_manager_mismatch")
    if state.get("nodeLinker") != _PORTABLE_NODE_LINKER:
        _fail("native_release_dependency_linker_mismatch")
    if state.get("pendingBuilds") != []:
        _fail("native_release_dependency_build_pending")

    projection = {
        key: value
        for key, value in state.items()
        if key not in INSTALL_ONLY_MODULE_FIELDS
    }
    _reject_windows_host_paths(projection)
    canonical = _canonical_json_bytes(projection)
    workspace_state = node_modules / _WORKSPACE_STATE_FILENAME
    if require_canonical_release:
        if any(key in state for key in INSTALL_ONLY_MODULE_FIELDS) or raw != canonical:
            _fail("native_release_dependency_metadata_noncanonical")
        if os.path.lexists(workspace_state):
            _fail("native_release_dependency_workspace_state_present")
    elif os.path.lexists(workspace_state):
        try:
            if _is_reparse_point(workspace_state) or not workspace_state.is_file():
                _fail("native_release_dependency_workspace_state_invalid")
        except OSError:
            _fail("native_release_dependency_workspace_state_invalid")
    return canonical


def _materialize_dependency_release_metadata(node_modules: Path) -> None:
    """Replace install-only pnpm state in a copied staging tree."""

    canonical = _read_dependency_release_metadata(
        node_modules,
        require_canonical_release=False,
    )
    modules_state = node_modules / _MODULES_STATE_FILENAME
    workspace_state = node_modules / _WORKSPACE_STATE_FILENAME
    try:
        modules_state.write_bytes(canonical)
        if os.path.lexists(workspace_state):
            workspace_state.unlink()
        for folder, dirs, names in os.walk(node_modules):
            parent = Path(folder)
            if any(_is_reparse_point(parent / name) for name in dirs):
                _fail("native_release_dependency_reparse_unsupported")
            if parent.name != ".bin":
                continue
            for name in names:
                path = parent / name
                if _is_reparse_point(path):
                    _fail("native_release_dependency_reparse_unsupported")
                relative = path.relative_to(node_modules).as_posix()
                release_name, shim_bytes = project_dependency_shim(relative, path)
                if shim_bytes is not None:
                    path.write_bytes(shim_bytes)
                if release_name != relative:
                    target = parent / Path(release_name).name
                    if any(other == target.name for other in names):
                        _fail("native_release_dependency_shim_name_collision")
                    path.rename(target)
    except DependencyPayloadError as error:
        _fail(error.code)
    except OSError:
        _fail("native_release_dependency_metadata_materialization_failed")


def _minimal_child_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return {
        name: source[name]
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")
        if source.get(name)
    }


def _run_dependency_runtime_gate(
    runtime_root: Path,
    node_executable: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve the direct graph and import production modules without I/O."""

    allowed_no_root_export = json.dumps(
        sorted(_NO_ROOT_EXPORT_DEPENDENCIES), separators=(",", ":"),
    )
    production_imports = json.dumps(
        list(_PRODUCTION_IMPORTS), separators=(",", ":"),
    )
    script = f"""
import fs from 'node:fs';
import path from 'node:path';
import {{ pathToFileURL }} from 'node:url';
const packageData = JSON.parse(fs.readFileSync('package.json', 'utf8'));
const dependencies = Object.keys(packageData.dependencies ?? {{}}).sort();
const allowedNoRootExport = new Set({allowed_no_root_export});
const nodeModulesUrl = pathToFileURL(path.resolve('node_modules') + path.sep).href;
const resolved = [];
for (const name of dependencies) {{
  try {{
    const url = import.meta.resolve(name);
    if (!url.startsWith(nodeModulesUrl)) throw new Error('resolution_escape');
    resolved.push({{ name, status: 'resolved' }});
  }} catch (error) {{
    if (error?.code === 'ERR_PACKAGE_PATH_NOT_EXPORTED' && allowedNoRootExport.has(name)) {{
      resolved.push({{ name, status: 'present_no_root_export' }});
      continue;
    }}
    throw error;
  }}
}}
const imported = [];
for (const relative of {production_imports}) {{
  await import(relative);
  imported.push(relative);
}}
process.stdout.write(JSON.stringify({{ resolved, imported }}));
"""
    try:
        result = subprocess.run(
            [str(node_executable), "--input-type=module", "--eval", script],
            cwd=runtime_root,
            env=_minimal_child_environment(environment),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        _fail("native_release_dependency_runtime_unavailable")
    if result.returncode != 0 or result.stderr.strip():
        _fail("native_release_dependency_runtime_failed")
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        _fail("native_release_dependency_runtime_failed")
    if not isinstance(receipt, dict):
        _fail("native_release_dependency_runtime_failed")
    resolved = receipt.get("resolved")
    imported = receipt.get("imported")
    expected_status = {
        name: (
            "present_no_root_export"
            if name in _NO_ROOT_EXPORT_DEPENDENCIES
            else "resolved"
        )
        for name in sorted(
            _read_json(
                runtime_root / "package.json", "native_release_package_invalid",
            )["dependencies"],
        )
    }
    if not isinstance(resolved, list) or len(resolved) != len(expected_status):
        _fail("native_release_dependency_runtime_failed")
    actual_status = {
        row.get("name"): row.get("status")
        for row in resolved
        if isinstance(row, dict)
    }
    if actual_status != expected_status or imported != list(_PRODUCTION_IMPORTS):
        _fail("native_release_dependency_runtime_failed")
    return {
        "direct_resolutions": len(resolved),
        "production_imports": len(imported),
    }


def _validate_dependency_closure(
    runtime_root: Path,
    *,
    require_canonical_release_metadata: bool = False,
    expected_cohort: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    node_modules = runtime_root / "node_modules"
    try:
        if not node_modules.is_dir():
            _fail("native_release_dependencies_missing")
        if _is_reparse_point(node_modules):
            _fail("native_release_dependency_reparse_unsupported")
        node_modules = node_modules.resolve(strict=True)
    except NativeAgentReleaseError:
        raise
    except OSError:
        _fail("native_release_dependencies_missing")

    package = _read_json(runtime_root / "package.json", "native_release_package_invalid")
    dependencies = package.get("dependencies")
    if not isinstance(dependencies, dict) or not dependencies:
        _fail("native_release_package_invalid")
    if not all(isinstance(name, str) and isinstance(version, str)
               for name, version in dependencies.items()):
        _fail("native_release_dependency_version_invalid")
    if expected_cohort is not None:
        if not isinstance(expected_cohort, Mapping):
            _fail("native_release_dependency_cohort_mismatch")
        actual = {name: version for name, version in dependencies.items()
                  if name.startswith("@deepseek-ai/")}
        reviewed = {name: row.get("version") for name, row in expected_cohort.items()
                    if isinstance(row, Mapping)}
        if actual != reviewed or len(reviewed) != len(expected_cohort):
            _fail("native_release_dependency_cohort_mismatch")
    if package.get("devDependencies") not in (None, {}):
        _fail("native_release_development_dependencies")
    for name, expected in dependencies.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            _fail("native_release_dependency_version_invalid")
        if re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", expected) is None:
            _fail("native_release_dependency_version_unpinned")
        installed = _read_json(
            node_modules.joinpath(*name.split("/"), "package.json"),
            "native_release_dependency_missing",
        )
        if installed.get("name") != name or installed.get("version") != expected:
            _fail("native_release_dependency_version_mismatch")

    canonical_metadata = _read_dependency_release_metadata(
        node_modules,
        require_canonical_release=require_canonical_release_metadata,
    )
    metrics = (
        _validate_link_graph(node_modules, require_canonical_shims=True)
        if require_canonical_release_metadata
        else _validate_link_graph(
            node_modules,
            projected_files={_MODULES_STATE_FILENAME: canonical_metadata},
            excluded_files=frozenset({_WORKSPACE_STATE_FILENAME}),
        )
    )
    return {
        "mode": NATIVE_AGENT_DEPENDENCY_MODE,
        "direct_dependencies": len(dependencies),
        "package_manager": NATIVE_AGENT_PACKAGE_MANAGER,
        "node_linker": _PORTABLE_NODE_LINKER,
        "metadata_normalization": DEPENDENCY_METADATA_POLICY,
        "reparse_points": 0,
        **metrics,
    }


@dataclass(frozen=True)
class NativeAgentBuildPlan:
    runtime_root: Path
    node_executable: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    identity: Mapping[str, Any]
    dependencies: Mapping[str, Any]
    product_app_version: str
    runtime_app_version: str
    release_blockers: tuple[str, ...]
    assets_root: Path | None = None

    @property
    def release_ready(self) -> bool:
        return not self.release_blockers

    def public(self) -> dict[str, Any]:
        """Return a reproducible, path-free build plan for audit output."""

        runtime_files = self.manifest.get("runtime_files", {})
        return {
            "contract": "genshin-native-agent-build-plan-v2",
            "state": "validated" if self.release_ready else "blocked",
            "release_ready": self.release_ready,
            "release_blockers": list(self.release_blockers),
            "product_app_version": self.product_app_version,
            "runtime_app_version": self.runtime_app_version,
            "runtime_generation": self.identity["runtime_generation"],
            "phase": self.identity["phase"],
            "manifest_sha256": self.identity["manifest_sha256"],
            "runtime_closure_hash": self.manifest["runtime_closure_hash"],
            "dependency_lock_hash": self.manifest["dependency_lock_hash"],
            "node_version": self.identity["node_version"],
            "node_binary_sha256": self.identity["node_binary_sha256"],
            "dsh_release": self.identity["dsh_release"],
            "project_runtime_files": len(runtime_files),
            "project_asset_files": len(self.manifest.get("asset_files", {})),
            "asset_closure_hash": self.manifest.get("asset_closure_hash"),
            "dependencies": dict(self.dependencies),
            "network_downloads": False,
            "path_discovery": False,
        }


def create_native_agent_build_plan(
    *,
    project_root: str | os.PathLike[str],
    native_node: str | os.PathLike[str] | None,
    environment: Mapping[str, str] | None = None,
) -> NativeAgentBuildPlan:
    """Validate all release inputs without modifying files or using network."""

    try:
        root = Path(project_root).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        _fail("native_release_project_root_missing")
    runtime_root = root / RUNTIME_DIRECTORY
    manifest_path = runtime_root / RUNTIME_MANIFEST
    assets_root = root / "frontend"
    node = resolve_explicit_node(native_node, environment=environment)
    identity = release_identity()
    if not isinstance(identity["manifest_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", identity["manifest_sha256"]) is None:
        _fail("native_release_shell_manifest_pin_missing")
    if _read_node_version(node, environment=environment) != identity["node_version"]:
        _fail("native_release_node_version_mismatch")
    if _sha256_file(node) != identity["node_binary_sha256"]:
        _fail("native_release_node_hash_mismatch")
    if _sha256_file(manifest_path) != identity["manifest_sha256"]:
        _fail("native_release_manifest_pin_mismatch")
    try:
        loaded = _load_dsh_client_shell_manifest(
            manifest_path,
            expected_sha256=identity["manifest_sha256"],
            runtime_root=runtime_root,
            assets_root=assets_root,
            node_executable=node,
        )
    except (NativeDshMuxError, RuntimeIntegrityError) as error:
        raise NativeAgentReleaseError(
            "native_release_runtime_verification_failed",
            cause_code=error.code,
        ) from None
    manifest = loaded.get("manifest")
    if not isinstance(manifest, Mapping):
        _fail("native_release_integrity_manifest_missing")
    for field in (
        "runtime_generation",
        "node_version",
        "node_binary_sha256",
        "dsh_release",
    ):
        if manifest.get(field) != identity[field]:
            _fail(f"native_release_identity_{field}_mismatch")
    dependencies = {
        **_validate_dependency_closure(runtime_root, expected_cohort=manifest["dependency_cohort"]),
        **_run_dependency_runtime_gate(
            runtime_root,
            node,
            environment=environment,
        ),
    }
    runtime_app_version = manifest.get("app_version")
    if not isinstance(runtime_app_version, str) or not runtime_app_version:
        _fail("native_release_runtime_app_version_missing")
    blockers: tuple[str, ...] = (
        ("native_release_app_version_mismatch",)
        if runtime_app_version != APP_VERSION
        else ()
    )
    return NativeAgentBuildPlan(
        runtime_root=runtime_root.resolve(strict=True),
        assets_root=assets_root.resolve(strict=True),
        node_executable=node,
        manifest_path=manifest_path.resolve(strict=True),
        manifest=manifest,
        identity=identity,
        dependencies=dependencies,
        product_app_version=APP_VERSION,
        runtime_app_version=runtime_app_version,
        release_blockers=blockers,
    )


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except OSError:
        _fail("native_release_stage_copy_failed")


def _copy_manifest_files(source: Path, target: Path, files, *, error_code: str) -> None:
    if not isinstance(files, Mapping) or not files:
        _fail(error_code)
    for relative in files:
        if not isinstance(relative, str) or _STAGE_RELATIVE_FILE.fullmatch(relative) is None:
            _fail(error_code)
    for relative in files:
        _copy_file(source.joinpath(*relative.split("/")), target.joinpath(*relative.split("/")))


def preserve_packaged_native_dependency_directories(
    *, staging_root: Path, package_root: Path, expected_directories: int,
) -> dict[str, Any]:
    """Publish authenticated directory structure omitted by file-only COLLECT.

    This is the sole directory publisher. It never copies, writes, replaces or
    deletes a file. Subsequent unchanged full payload/layout gates still reject
    missing or altered files and additional directories.
    """
    if type(expected_directories) is not int or expected_directories < 0:
        _fail("native_release_directory_count_invalid")

    def checked_directory(path: Path) -> Path:
        declared = Path(path).absolute()
        try:
            canonical = declared.resolve(strict=True)
            if (os.path.normcase(str(declared)) != os.path.normcase(str(canonical))
                    or not declared.is_dir() or _is_reparse_point(declared)):
                _fail("native_release_directory_publication_path_invalid")
        except NativeAgentReleaseError:
            raise
        except (OSError, ValueError):
            _fail("native_release_directory_publication_path_invalid")
        return canonical

    stage, package = checked_directory(staging_root), checked_directory(package_root)
    if stage == package or stage in package.parents or package in stage.parents:
        _fail("native_release_directory_publication_roots_overlap")
    source = checked_directory(checked_directory(stage / RUNTIME_DIRECTORY) / "node_modules")
    internal = checked_directory(package / "_internal")
    target = checked_directory(checked_directory(internal / RUNTIME_DIRECTORY) / "node_modules")
    relative_directories = []
    pending = [source]
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if _is_reparse_point(path):
                        _fail("native_release_dependency_reparse_unsupported")
                    if entry.is_dir(follow_symlinks=False):
                        relative_directories.append(path.relative_to(source))
                        pending.append(path)
        if len(relative_directories) != expected_directories:
            _fail("native_release_directory_publication_source_count_mismatch")
        # Validate the entire proposed publication before creating anything.
        for relative in relative_directories:
            destination = target / relative
            _inside(target, destination, "native_release_directory_publication_escape")
            if os.path.lexists(destination):
                checked_directory(destination)
        created = 0
        for relative in sorted(relative_directories, key=lambda item: (len(item.parts), item.parts)):
            destination = target / relative
            checked_directory(destination.parent)
            if not os.path.lexists(destination):
                destination.mkdir()
                created += 1
            checked_directory(destination)
    except NativeAgentReleaseError:
        raise
    except (OSError, ValueError):
        _fail("native_release_directory_publication_failed")
    return {"contract": "genshin-native-directory-publication-v1",
            "directories": expected_directories, "created_directories": created,
            "files_written": 0}


def prepare_native_agent_stage(
    plan: NativeAgentBuildPlan,
    *,
    staging_root: str | os.PathLike[str],
    allowed_parent: str | os.PathLike[str],
) -> Path:
    """Create the exact PyInstaller staging tree and revalidate its identity."""

    if not plan.release_ready:
        raise NativeAgentReleaseError(
            "native_release_plan_blocked",
            cause_code=plan.release_blockers[0],
        )
    try:
        allowed = Path(allowed_parent).resolve(strict=True)
        stage = Path(staging_root).absolute()
        stage.relative_to(allowed)
    except (OSError, TypeError, ValueError):
        _fail("native_release_stage_outside_build_root")
    if stage == allowed:
        _fail("native_release_stage_outside_build_root")
    if plan.identity.get("phase") != "dsh_client_shell":
        _fail("native_release_phase_retired")
    if stage.exists():
        try:
            canonical = stage.resolve(strict=True)
            canonical.relative_to(allowed)
        except (OSError, ValueError):
            _fail("native_release_stage_outside_build_root")
        try:
            shutil.rmtree(stage)
        except OSError:
            _fail("native_release_stage_cleanup_failed")
    if plan.assets_root is None:
        _fail("native_release_assets_missing")
    runtime_stage = stage / RUNTIME_DIRECTORY
    assets_stage = stage / ASSETS_DIRECTORY
    release_stage = stage / RELEASE_IDENTITY_DIRECTORY
    runtime_stage.mkdir(parents=True)
    assets_stage.mkdir(parents=True)
    release_stage.mkdir(parents=True)

    _copy_manifest_files(plan.runtime_root, runtime_stage, plan.manifest.get("runtime_files"),
                         error_code="native_release_manifest_files_invalid")
    _copy_manifest_files(plan.assets_root, assets_stage, plan.manifest.get("asset_files"),
                         error_code="native_release_manifest_assets_invalid")
    _copy_file(plan.manifest_path, runtime_stage / RUNTIME_MANIFEST)
    _copy_file(plan.node_executable, runtime_stage / "node.exe")
    try:
        shutil.copytree(
            plan.runtime_root / "node_modules",
            runtime_stage / "node_modules",
            symlinks=False,
            copy_function=shutil.copy2,
        )
    except OSError:
        _fail("native_release_dependency_stage_failed")
    _materialize_dependency_release_metadata(runtime_stage / "node_modules")

    identity_text = json.dumps(
        dict(plan.identity),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    plan_text = json.dumps(
        plan.public(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    try:
        (release_stage / RELEASE_IDENTITY_FILENAME).write_text(
            identity_text,
            encoding="utf-8",
        )
        (release_stage / BUILD_PLAN_FILENAME).write_text(plan_text, encoding="utf-8")
    except OSError:
        _fail("native_release_stage_write_failed")

    # Copying must not weaken or silently change either trust boundary.
    if _sha256_file(runtime_stage / RUNTIME_MANIFEST) != plan.identity["manifest_sha256"]:
        _fail("native_release_stage_manifest_mismatch")
    if _sha256_file(runtime_stage / "node.exe") != plan.identity["node_binary_sha256"]:
        _fail("native_release_stage_node_mismatch")
    try:
        staged = _load_dsh_client_shell_manifest(
            runtime_stage / RUNTIME_MANIFEST,
            expected_sha256=plan.identity["manifest_sha256"],
            runtime_root=runtime_stage,
            assets_root=assets_stage,
            node_executable=runtime_stage / "node.exe",
        )
    except (NativeDshMuxError, RuntimeIntegrityError) as error:
        raise NativeAgentReleaseError(
            "native_release_stage_verification_failed",
            cause_code=error.code,
        ) from None
    staged_manifest = staged.get("manifest")
    if not isinstance(staged_manifest, Mapping) or (
        staged_manifest.get("runtime_closure_hash")
        != plan.manifest.get("runtime_closure_hash")
        or staged_manifest.get("asset_closure_hash")
        != plan.manifest.get("asset_closure_hash")
    ):
        _fail("native_release_stage_closure_mismatch")
    staged_dependencies = {
        **_validate_dependency_closure(
            runtime_stage,
            require_canonical_release_metadata=True,
            expected_cohort=plan.manifest["dependency_cohort"],
        ),
        **_run_dependency_runtime_gate(
            runtime_stage,
            runtime_stage / "node.exe",
        ),
    }
    if staged_dependencies != dict(plan.dependencies):
        raise NativeDependencyLayoutError("native_release_stage_dependency_layout_mismatch",
            expected=plan.dependencies, actual=staged_dependencies)
    return stage


def validate_packaged_native_agent_runtime(
    plan: NativeAgentBuildPlan,
    *,
    package_root: str | os.PathLike[str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify the final PyInstaller runtime before publishing any receipt."""

    try:
        root = Path(package_root).resolve(strict=True)
        if root.is_symlink() or not root.is_dir():
            _fail("native_release_package_root_invalid")
        runtime_root = root / "_internal" / RUNTIME_DIRECTORY
        release_root = root / "_internal" / RELEASE_IDENTITY_DIRECTORY
        assets_root = root / "_internal" / ASSETS_DIRECTORY
        canonical_runtime = runtime_root.resolve(strict=True)
        _inside(root, canonical_runtime, "native_release_package_runtime_escape")
        if os.path.normcase(str(runtime_root)) != os.path.normcase(
            str(canonical_runtime),
        ):
            _fail("native_release_package_runtime_alias")
    except NativeAgentReleaseError:
        raise
    except (OSError, TypeError, ValueError):
        _fail("native_release_package_runtime_missing")

    manifest_path = runtime_root / RUNTIME_MANIFEST
    node_executable = runtime_root / "node.exe"
    if _sha256_file(manifest_path) != plan.identity["manifest_sha256"]:
        _fail("native_release_package_manifest_mismatch")
    if _sha256_file(node_executable) != plan.identity["node_binary_sha256"]:
        _fail("native_release_package_node_mismatch")
    if _read_node_version(
        node_executable,
        environment=environment,
    ) != plan.identity["node_version"]:
        _fail("native_release_package_node_version_mismatch")
    try:
        packaged = _load_dsh_client_shell_manifest(
            manifest_path,
            expected_sha256=plan.identity["manifest_sha256"],
            runtime_root=runtime_root,
            assets_root=assets_root,
            node_executable=node_executable,
        )
    except (NativeDshMuxError, RuntimeIntegrityError) as error:
        raise NativeAgentReleaseError(
            "native_release_package_runtime_verification_failed",
            cause_code=error.code,
        ) from None
    packaged_manifest = packaged.get("manifest")
    if not isinstance(packaged_manifest, Mapping) or (
        packaged_manifest.get("runtime_closure_hash")
        != plan.manifest.get("runtime_closure_hash")
        or packaged_manifest.get("asset_closure_hash")
        != plan.manifest.get("asset_closure_hash")
    ):
        _fail("native_release_package_closure_mismatch")

    if _read_json(
        release_root / RELEASE_IDENTITY_FILENAME,
        "native_release_package_identity_missing",
    ) != dict(plan.identity):
        _fail("native_release_package_identity_mismatch")
    if _read_json(
        release_root / BUILD_PLAN_FILENAME,
        "native_release_package_plan_missing",
    ) != plan.public():
        _fail("native_release_package_plan_mismatch")

    dependencies = {
        **_validate_dependency_closure(
            runtime_root,
            require_canonical_release_metadata=True,
            expected_cohort=plan.manifest["dependency_cohort"],
        ),
        **_run_dependency_runtime_gate(
            runtime_root,
            node_executable,
            environment=environment,
        ),
    }
    if dependencies != dict(plan.dependencies):
        raise NativeDependencyLayoutError("native_release_package_dependency_layout_mismatch",
            expected=plan.dependencies, actual=dependencies)
    return {
        "contract": "genshin-packaged-native-agent-validation-v1",
        "runtime_generation": plan.identity["runtime_generation"],
        "manifest_sha256": plan.identity["manifest_sha256"],
        "node_binary_sha256": plan.identity["node_binary_sha256"],
        "dependencies": dependencies,
        "asset_closure_hash": packaged_manifest["asset_closure_hash"],
        "project_asset_files": len(packaged_manifest["asset_files"]),
    }


def validate_staged_native_agent_runtime(staging_root) -> dict[str, Any]:
    """Reconstruct and verify both roots for the spec, without starting an Agent."""
    try:
        declared = Path(staging_root).absolute()
        stage = declared.resolve(strict=True)
        if not stage.is_dir() or os.path.normcase(str(stage)) != os.path.normcase(str(declared)):
            _fail("native_release_stage_root_invalid")
    except NativeAgentReleaseError:
        raise
    except (OSError, TypeError, ValueError):
        _fail("native_release_stage_root_invalid")
    runtime, assets = stage / RUNTIME_DIRECTORY, stage / ASSETS_DIRECTORY
    release = stage / RELEASE_IDENTITY_DIRECTORY
    identity = release_identity()
    if not isinstance(identity["manifest_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", identity["manifest_sha256"]) is None:
        _fail("native_release_shell_manifest_pin_missing")
    if _read_json(release / RELEASE_IDENTITY_FILENAME, "native_release_stage_identity_missing") != identity:
        _fail("native_release_stage_identity_mismatch")
    node = runtime / "node.exe"
    if _sha256_file(node) != identity["node_binary_sha256"] or _read_node_version(node) != identity["node_version"]:
        _fail("native_release_stage_node_mismatch")
    try:
        verified = _load_dsh_client_shell_manifest(
            runtime / RUNTIME_MANIFEST, expected_sha256=identity["manifest_sha256"],
            runtime_root=runtime, assets_root=assets, node_executable=node,
        )
    except (NativeDshMuxError, RuntimeIntegrityError) as error:
        raise NativeAgentReleaseError("native_release_stage_verification_failed", cause_code=error.code) from None
    manifest = verified["manifest"]
    dependencies = {
        **_validate_dependency_closure(runtime, require_canonical_release_metadata=True,
                                       expected_cohort=manifest["dependency_cohort"]),
        **_run_dependency_runtime_gate(runtime, node),
    }
    current = NativeAgentBuildPlan(
        runtime_root=runtime, assets_root=assets, node_executable=node,
        manifest_path=runtime / RUNTIME_MANIFEST, manifest=manifest, identity=identity,
        dependencies=dependencies, product_app_version=APP_VERSION,
        runtime_app_version=manifest["app_version"], release_blockers=(),
    )
    if _read_json(release / BUILD_PLAN_FILENAME, "native_release_stage_plan_missing") != current.public():
        _fail("native_release_stage_plan_mismatch")
    return {"contract": "genshin-native-agent-stage-validation-v2",
            "phase": "dsh_client_shell", "manifest_sha256": identity["manifest_sha256"],
            "runtime_closure_hash": manifest["runtime_closure_hash"],
            "asset_closure_hash": manifest["asset_closure_hash"],
            "dependencies": dependencies, "agent_started": False}


def cleanup_native_agent_stage(
    staging_root: str | os.PathLike[str],
    *,
    allowed_parent: str | os.PathLike[str],
) -> None:
    """Delete only the validated staging child after PyInstaller returns."""

    stage = Path(staging_root).absolute()
    try:
        allowed = Path(allowed_parent).resolve(strict=True)
        canonical = stage.resolve(strict=True)
        canonical.relative_to(allowed)
    except FileNotFoundError:
        return
    except (OSError, TypeError, ValueError):
        _fail("native_release_stage_outside_build_root")
    if canonical == allowed:
        _fail("native_release_stage_outside_build_root")
    try:
        shutil.rmtree(canonical)
    except OSError:
        _fail("native_release_stage_cleanup_failed")


def write_build_plan(path: str | os.PathLike[str], plan: NativeAgentBuildPlan) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            plan.public(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "BUILD_PLAN_FILENAME",
    "CONTROLLED_NODE_ENV",
    "RELEASE_IDENTITY_DIRECTORY",
    "RELEASE_IDENTITY_FILENAME",
    "NativeAgentBuildPlan",
    "NativeAgentReleaseError",
    "cleanup_native_agent_stage",
    "create_native_agent_build_plan",
    "prepare_native_agent_stage",
    "release_identity",
    "resolve_explicit_node",
    "validate_packaged_native_agent_runtime",
    "validate_staged_native_agent_runtime",
    "write_build_plan",
]
