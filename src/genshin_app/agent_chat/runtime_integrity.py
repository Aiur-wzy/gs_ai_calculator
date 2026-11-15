"""Release-grade integrity checks for the bundled native-Agent runtime."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_RELATIVE_FILE = re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._@+/-]+\Z")
_MAX_LOCAL_FILE_BYTES = 64 * 1024 * 1024
_SELFCHECK_ENTRY = "src/native-dsh-selfcheck-entry.mjs"
_SELFCHECK_DEFERRED_IMPORTS = frozenset({
    "src/native-dsh-selfcheck-provider.mjs", "src/native-mux-bootstrap.mjs",
    "src/native-dsh-session-host.mjs", "src/native-dsh-mux-gateway.mjs",
    "src/native-dsh-domain-transport.mjs", "src/native-dsh-runtime-config.mjs",
})

DSH_CLIENT_SHELL_RUNTIME_FILES = tuple(sorted((
    ".npmrc", "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    "patches/@deepseek-ai__cordis@4.0.5.patch", "src/dsh-task-review.mjs", "src/publication-usage.mjs",
    "src/native-app-tool-catalog.mjs", "src/native-app-tool-schema.mjs", "src/native-app-system-failure.mjs",
    "src/bridge-envelope.mjs", "src/dsh-client-shell-runtime-contract.mjs",
    "src/dependency-payloads.mjs",
    "src/fixed-root-filesystem-contract.mjs", "src/fixed-root-filesystem.mjs",
    "src/memory-credentials.mjs",
    "src/native-deepseek-kernel.mjs", "src/native-dsh-accounting.mjs", "src/native-dsh-client-assets.mjs",
    "src/native-dsh-domain-tools.mjs", "src/native-dsh-domain-transport.mjs",
    "src/native-dsh-app-context-projection.mjs",
    "src/native-dsh-input-snapshot.mjs", "src/native-dsh-full-profile.mjs", "src/native-dsh-app-settings.mjs",
    "src/native-dsh-profile-config.mjs", "src/native-dsh-task-transport.mjs", "src/native-dsh-job-drain.mjs",
    "src/native-dsh-logical-transport.mjs", "src/native-logical-stream.mjs",
    "src/native-dsh-profile-settings.mjs",
    "src/native-dsh-generic-capabilities.mjs", "src/native-dsh-mux-gateway.mjs",
    "src/native-dsh-runtime-config.mjs", "src/native-dsh-runtime-entry.mjs",
    "src/native-dsh-runtime.mjs", "src/native-dsh-session-host.mjs",
    "src/native-dsh-selfcheck-entry.mjs", "src/native-dsh-selfcheck-guards.mjs", "src/native-dsh-selfcheck-provider.mjs",
    "src/native-dsh-settings.mjs", "src/native-dsh-web-stack.mjs",
    "src/native-mux-bootstrap.mjs", "src/native-mux-launch.mjs",
    "src/native-mux-pipe-client.mjs", "src/native-mux-protocol.mjs",
    "src/native-pipe-multiplexer.mjs",
    "src/native-provider-admission.mjs", "src/native-provider-proxy.mjs",
    "src/native-retry-admission.mjs", "src/native-shell-credentials.mjs",
    "src/production-composition-contract.mjs", "src/runtime-integrity.mjs",
    "src/runtime-version.mjs", "src/secret-control.mjs",
)))



class RuntimeIntegrityError(RuntimeError):
    """Sanitized integrity failure with a stable machine code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RuntimeIntegrityError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        _fail("runtime_integrity_file_read")
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail("runtime_integrity_json")


def _same_path(left: Path, right: Path) -> bool:
    first = os.path.normcase(str(left.absolute()))
    second = os.path.normcase(str(right.absolute()))
    return first == second


def _strict_root(value: str | Path) -> Path:
    root = Path(value).absolute()
    try:
        if not root.is_dir() or root.is_symlink():
            _fail("runtime_integrity_root_alias")
        canonical = root.resolve(strict=True)
    except OSError:
        _fail("runtime_integrity_root_missing")
    if not _same_path(root, canonical):
        _fail("runtime_integrity_root_alias")
    return root


def _validate_file_list(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        _fail("runtime_integrity_file_list")
    checked = tuple(value)
    for item in checked:
        if not isinstance(item, str) or "\\" in item or _RELATIVE_FILE.fullmatch(item) is None:
            _fail("runtime_integrity_file_name")
    if checked != tuple(sorted(set(checked))):
        _fail("runtime_integrity_file_order")
    if "pnpm-lock.yaml" not in checked or "src/runtime-integrity.mjs" not in checked:
        _fail("runtime_integrity_file_list")
    return checked


def _assert_known_critical_entries(
    root: Path,
    *,
    admitted: Sequence[str],
) -> None:
    try:
        names = [item.name for item in (root / "src").iterdir()]
    except OSError:
        _fail("runtime_integrity_src_missing")
    sidecars = tuple(sorted(name for name in names
                            if re.fullmatch(r"native-(?:.*-)?sidecar-entry\.mjs", name)))
    contracts = tuple(sorted(name for name in names
                             if re.fullmatch(r"phase.*-runtime-contract\.mjs", name)))
    admitted_names = tuple(Path(item).name for item in admitted)
    admitted_sidecars = tuple(sorted(
        name for name in admitted_names
        if re.fullmatch(r"native-(?:.*-)?sidecar-entry\.mjs", name)
    ))
    admitted_contracts = tuple(sorted(
        name for name in admitted_names
        if re.fullmatch(r"phase.*-runtime-contract\.mjs", name)
    ))
    # Both development and release admit only the current execution closure.
    # Retired sidecars cannot return through an old full-phase whitelist.
    if sidecars != admitted_sidecars:
        _fail("runtime_integrity_unknown_sidecar_entry")
    if contracts != admitted_contracts:
        _fail("runtime_integrity_unknown_contract_entry")

def _assert_local_import_closure(
    *, root: Path, filename: str, source: str, admitted: frozenset[str],
) -> None:
    # Guard installation must precede DSH module evaluation in the named
    # offline selfcheck. Only its six reviewed, literal local loads are allowed.
    dynamic = re.compile(r"\bimport(?:\s|/\*[\s\S]*?\*/|//[^\r\n]*(?:\r?\n|$))*\(")
    for match in dynamic.finditer(source):
        literal = re.match(r"import\s*\(\s*(['\"])(\.[A-Za-z0-9._/-]*)\1\s*\)", source[match.start():])
        if (filename != _SELFCHECK_ENTRY or literal is None
                or re.search(r"\bawait\s*$", source[:match.start()]) is None):
            _fail("runtime_integrity_dynamic_import")
        target = (root / Path(filename).parent / literal.group(2)).resolve()
        try:
            imported = target.relative_to(root).as_posix()
        except ValueError:
            _fail("runtime_integrity_import_escape")
        if imported not in admitted or imported not in _SELFCHECK_DEFERRED_IMPORTS:
            _fail("runtime_integrity_unlisted_import")
    if re.search(
        r"\b(?:createRequire|require|getBuiltinModule)\s*(?:\?\.)?\s*\(", source,
    ) or re.search(
        r"\bprocess\s*(?:\?\.)?\s*(?:\.\s*getBuiltinModule|"
        r"\[\s*['\"]getBuiltinModule['\"]\s*\])",
        source,
    ):
        _fail("runtime_integrity_ambient_loader")
    pattern = re.compile(
        r"\bfrom\s*['\"](\.[^'\"]+)['\"]|\bimport\s*['\"](\.[^'\"]+)['\"]",
    )
    for match in pattern.finditer(source):
        specifier = match.group(1) or match.group(2)
        target = (root / Path(filename).parent / specifier).absolute()
        try:
            imported = target.relative_to(root).as_posix()
        except ValueError:
            _fail("runtime_integrity_import_escape")
        if imported not in admitted:
            _fail("runtime_integrity_unlisted_import")

    # Loader entries expressed by local URL are executable modules, not
    # optional assets. Keep this independent check aligned with Node.
    module_urls = re.compile(
        r"\bnew\s+URL\s*\(\s*['\"](\.[^'\"]+\.mjs)['\"]\s*,\s*import\.meta\.url\s*\)",
    )
    for match in module_urls.finditer(source):
        target = (root / Path(filename).parent / match.group(1)).resolve()
        try:
            imported = target.relative_to(root).as_posix()
        except ValueError:
            _fail("runtime_integrity_import_escape")
        if imported not in admitted:
            _fail("runtime_integrity_unlisted_import")


def audit_runtime_closure(
    *, phase: str, files: Sequence[str], root: str | Path,
) -> dict[str, Any]:
    """Hash the exact project-owned files for the current DSH shell."""
    if phase != "dsh_client_shell":
        _fail("runtime_integrity_phase")
    checked_files = _validate_file_list(files)
    absolute = _strict_root(root)
    _assert_known_critical_entries(absolute, admitted=checked_files)
    hashes: dict[str, str] = {}
    sources: dict[str, str] = {}
    for filename in checked_files:
        target = absolute.joinpath(*filename.split("/"))
        try:
            if target.is_symlink() or not target.is_file():
                _fail("runtime_integrity_file_type")
            if target.stat().st_size > _MAX_LOCAL_FILE_BYTES:
                _fail("runtime_integrity_file_type")
            canonical = target.resolve(strict=True)
        except OSError:
            _fail("runtime_integrity_file_missing")
        try:
            canonical.relative_to(absolute)
        except ValueError:
            _fail("runtime_integrity_file_escape")
        if not _same_path(target, canonical):
            _fail("runtime_integrity_file_alias")
        hashes[filename] = _sha256_file(target)
        if filename.endswith(".mjs"):
            try:
                sources[filename] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                _fail("runtime_integrity_file_read")
    admitted = frozenset(checked_files)
    for filename, source in sources.items():
        _assert_local_import_closure(
            root=absolute, filename=filename, source=source, admitted=admitted,
        )
    dependency_lock_hash = hashes["pnpm-lock.yaml"]
    runtime_closure_hash = hashlib.sha256(_canonical_json({
        "contract": "genshin-native-runtime-closure-v1",
        "phase": phase,
        "file_hashes": list(hashes.items()),
    })).hexdigest()
    return {
        "contract": "genshin-native-runtime-closure-v1",
        "phase": phase,
        "files": hashes,
        "dependency_lock_hash": dependency_lock_hash,
        "runtime_closure_hash": runtime_closure_hash,
    }


def audit_node_executable(path: str | Path) -> dict[str, str]:
    """Hash one exact, non-aliased Node executable."""
    target = Path(path).absolute()
    try:
        if target.is_symlink() or not target.is_file():
            _fail("runtime_integrity_node_type")
        canonical = target.resolve(strict=True)
    except OSError:
        _fail("runtime_integrity_node_missing")
    if not _same_path(target, canonical):
        _fail("runtime_integrity_node_alias")
    return {"path": str(canonical), "node_binary_sha256": _sha256_file(target)}


def verify_runtime_integrity(
    manifest: Mapping[str, Any], *, phase: str, runtime_root: str | Path,
    node_executable: str | Path,
    assets_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the sole current production identity; no old-phase fallback."""
    if phase != "dsh_client_shell":
        _fail("runtime_integrity_phase")
    if assets_root is None:
        _fail("dsh_shell_asset_root")
    return verify_dsh_client_shell_manifest(
        manifest, runtime_root=runtime_root, assets_root=assets_root,
        node_executable=node_executable)


def verify_dsh_client_shell_manifest(
    manifest: Mapping[str, Any], *, runtime_root: str | Path,
    assets_root: str | Path, node_executable: str | Path,
) -> dict[str, Any]:
    """Verify both independently rooted execution surfaces of the new shell."""
    from genshin_app.version import APP_VERSION, NATIVE_AGENT_NODE_VERSION

    fields = {
        "contract", "phase", "app_version", "runtime_generation", "entry", "node_version",
        "dsh_release", "runtime_files", "runtime_closure_hash", "dependency_lock_hash",
        "node_binary_sha256", "dependency_cohort", "dependency_payloads", "asset_files", "asset_closure_hash",
        "client_composition_hash", "client_accepted_hash", "client_accepted_semantic_hash", "manifest_hash",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != fields:
        _fail("dsh_shell_manifest_shape")
    body = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if hashlib.sha256(_canonical_json(body)).hexdigest() != manifest["manifest_hash"]:
        _fail("dsh_shell_manifest_hash")
    if (manifest["contract"] != "genshin-native-dsh-client-shell-manifest-v1"
            or manifest["phase"] != manifest["runtime_generation"] or manifest["phase"] != "dsh_client_shell"
            or manifest["app_version"] != APP_VERSION or manifest["node_version"] != NATIVE_AGENT_NODE_VERSION
            or manifest["dsh_release"] != "0.2.1-rc.5" or manifest["entry"] != "src/native-dsh-runtime-entry.mjs"):
        _fail("dsh_shell_manifest_identity")
    root = _strict_root(runtime_root)
    from .native_dsh_dependency_payloads import (
        DependencyPayloadError,
        dependency_payload_inventory,
        project_dependency_shim,
    )
    try:
        if manifest["dependency_payloads"] != dependency_payload_inventory(root):
            _fail("dsh_shell_dependency_payload_mismatch")
    except (DependencyPayloadError, OSError, ValueError):
        _fail("dsh_shell_dependency_payload_invalid")
    closure = audit_runtime_closure(phase="dsh_client_shell", files=DSH_CLIENT_SHELL_RUNTIME_FILES, root=root)
    node = audit_node_executable(node_executable)
    if (manifest["runtime_files"] != closure["files"]
            or manifest["runtime_closure_hash"] != closure["runtime_closure_hash"]
            or manifest["dependency_lock_hash"] != closure["dependency_lock_hash"]
            or manifest["node_binary_sha256"] != node["node_binary_sha256"]):
        _fail("dsh_shell_runtime_evidence_mismatch")
    if any(re.search(r"probe|fixture|fake|qualification|sidecar-dev", name)
           for name in DSH_CLIENT_SHELL_RUNTIME_FILES):
        _fail("dsh_shell_nonproduction_closure")

    assets = _strict_root(assets_root)

    def read(name: str) -> bytes:
        if not isinstance(name, str) or _RELATIVE_FILE.fullmatch(name) is None:
            _fail("dsh_shell_asset_file_name")
        path = assets.joinpath(*name.split("/"))
        try:
            if not path.is_file() or path.is_symlink():
                _fail("dsh_shell_asset_type")
            if not _same_path(path, path.resolve(strict=True)):
                _fail("dsh_shell_asset_alias")
            return path.read_bytes()
        except OSError:
            _fail("dsh_shell_asset_missing")

    def descend(prefix: str) -> list[str]:
        path = assets.joinpath(*prefix.split("/"))
        if not path.is_dir() or path.is_symlink():
            _fail("dsh_shell_asset_type")
        result: list[str] = []
        for item in path.rglob("*"):
            if item.is_symlink():
                _fail("dsh_shell_asset_type")
            if item.is_file():
                result.append(item.relative_to(assets).as_posix())
        return result

    composition_bytes = read("dsh-client-composition.json")
    accepted_bytes = read("dsh-client-accepted-manifest.json")
    try:
        composition = json.loads(composition_bytes)
        accepted = json.loads(accepted_bytes)
        accepted_semantic = accepted["semanticSha256"]
        accepted_unsigned = {key: value for key, value in accepted.items() if key != "semanticSha256"}
        if (composition["dshRelease"] != "0.2.1-rc.5" or accepted["dshRelease"] != "0.2.1-rc.5"
                or composition["dshCommit"] != accepted["dshCommit"]
                or composition["dynamicModules"] != accepted["entryOrder"]):
            _fail("dsh_shell_client_cohort")
        unsigned_bytes = json.dumps(accepted_unsigned, ensure_ascii=False, separators=(",", ":")).encode()
        if hashlib.sha256(unsigned_bytes).hexdigest() != accepted_semantic:
            _fail("dsh_shell_client_accepted_digest")
        names = ["dsh-client-composition.json", "dsh-client-accepted-manifest.json", "package.json", "pnpm-lock.yaml",
                 *descend("dist")]
        for local in composition.get("localModules", {}).values():
            entry = local["hostEntry"]
            if not isinstance(entry, str) or _RELATIVE_FILE.fullmatch(entry) is None:
                _fail("dsh_shell_local_module_path")
            folder = str(Path(entry).parent).replace("\\", "/")
            names.extend([entry, folder + "/package.json", *descend(folder + "/lib")])
        files = {name: hashlib.sha256(read(name)).hexdigest() for name in sorted(set(names))}
        if not {"dist/index.html", "dist/asset-manifest.json"} <= set(files):
            _fail("dsh_shell_platform_build_missing")
        package = json.loads((root / "package.json").read_bytes())
        cohort = {}
        for name, version in sorted(package["dependencies"].items()):
            if not name.startswith("@deepseek-ai/"):
                continue
            if (name == "@deepseek-ai/dsh" or name.startswith("@deepseek-ai/dsh-")) and version != "0.2.1-rc.5":
                _fail("dsh_shell_dependency_cohort_mixed")
            content = (root / "node_modules" / name / "package.json").read_bytes()
            dependency = json.loads(content)
            if dependency["name"] != name or dependency["version"] != version:
                _fail("dsh_shell_dependency_identity")
            package_root = (root / "node_modules" / name).resolve(strict=True)
            package_files = {}
            for item in sorted(package_root.rglob("*")):
                if item.is_symlink():
                    _fail("dsh_shell_dependency_alias")
                if item.is_file():
                    relative_name = item.relative_to(package_root).as_posix()
                    if _RELATIVE_FILE.fullmatch(relative_name) is None:
                        _fail("dsh_shell_dependency_file_name")
                    release_name, projected = project_dependency_shim(relative_name, item)
                    if release_name in package_files:
                        _fail("dsh_dependency_shim_name_collision")
                    package_files[release_name] = (_sha256_file(item) if projected is None
                                                   else hashlib.sha256(projected).hexdigest())
            cohort[name] = {"version": version, "package_json_sha256": hashlib.sha256(content).hexdigest(),
                            "files": package_files}
    except (DependencyPayloadError, ValueError, KeyError, TypeError, OSError):
        _fail("dsh_shell_asset_evidence_invalid")
    asset_hash = hashlib.sha256(_canonical_json({
        "contract": "genshin-native-dsh-client-assets-closure-v1", "file_hashes": list(files.items()),
    })).hexdigest()
    if (manifest["asset_files"] != files or manifest["asset_closure_hash"] != asset_hash
            or manifest["client_composition_hash"] != hashlib.sha256(composition_bytes).hexdigest()
            or manifest["client_accepted_hash"] != hashlib.sha256(accepted_bytes).hexdigest()
            or manifest["client_accepted_semantic_hash"] != accepted_semantic or manifest["dependency_cohort"] != cohort):
        _fail("dsh_shell_asset_evidence_mismatch")
    return {"closure": closure, "node": node, "assets": {
        "root": str(assets), "files": files, "asset_closure_hash": asset_hash,
        "client_accepted_semantic_hash": accepted_semantic,
    }, "dependency_cohort": cohort, "manifest_hash": manifest["manifest_hash"]}


__all__ = [
    "DSH_CLIENT_SHELL_RUNTIME_FILES",
    "RuntimeIntegrityError",
    "audit_node_executable",
    "audit_runtime_closure",
    "verify_dsh_client_shell_manifest",
    "verify_runtime_integrity",
]
