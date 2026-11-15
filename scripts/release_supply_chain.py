"""Offline, deterministic release supply-chain materials.

The generator deliberately inventories the files already selected for a
release.  It never calls a package manager, registry, or network service and
it does not infer a licence grant from public availability.  The bundled
game-data database therefore remains ``NOASSERTION`` in machine-readable
materials, while its legal and redistribution assessment is owned by the
user rather than enforced as an engineering release gate under ADR-0015.

Two kinds of evidence are produced:

* ``sbom.cdx.json`` is a path-free CycloneDX 1.5 component inventory.
* ``license-manifest.json`` and ``THIRD_PARTY_NOTICES.txt`` record declared
  licence metadata, the exact licence texts found in the installed artefacts,
  and every item that still needs human review.

After PyInstaller finishes, :func:`write_package_file_manifest` creates the
sorted SHA-256 inventory of the actual portable directory.  The manifest
excludes itself so it has a stable, non-recursive definition.  The separate
:func:`verify_package_file_manifest` reader never rewrites that evidence: it
parses the sealed rows and independently walks and hashes the package.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_app.package_integrity import (
    PACKAGE_MANIFEST_DEFINITION,
    PACKAGE_MANIFEST_EXCLUDED_PATHS,
    PACKAGE_MANIFEST_FILENAME,
    PackageIntegrityError,
    verify_package_artifact,
)
from scripts.release_license_evidence import (
    LicenseEvidenceError,
    bind_license_evidence,
    load_license_evidence,
    public_review,
)

NOTICE_FILENAME = "THIRD_PARTY_NOTICES.txt"
SBOM_FILENAME = "sbom.cdx.json"
LICENSE_MANIFEST_FILENAME = "license-manifest.json"
_PACKAGE_MANIFEST_EXCLUDED_PATHS = PACKAGE_MANIFEST_EXCLUDED_PATHS
SUPPLY_CHAIN_RECEIPT_FILENAME = "supply-chain-receipt.json"
LICENSE_REVIEW_FILENAME = "license-review.json"
DEFAULT_LICENSE_EVIDENCE = ROOT / "third_party" / "reviewed-licenses" / "manifest.json"
DATABASE_ADR = "docs/decisions/0015-上游数据库合规评估由用户管理.md"
DATABASE_BASELINE = "docs/maintenance/database-upstream-version-baseline.md"
_LICENSE_PREFIXES = ("license", "licence", "copying", "notice")
_MAX_METADATA_BYTES = 4 * 1024 * 1024


class SupplyChainError(RuntimeError):
    """Offline supply-chain generation failed with a stable code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise SupplyChainError(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        _fail("supply_chain_file_unreadable")
    return digest.hexdigest()


def _read_small_bytes(path: Path) -> bytes:
    try:
        if _is_reparse_point(path) or not path.is_file():
            _fail("supply_chain_metadata_invalid")
        if path.stat().st_size > _MAX_METADATA_BYTES:
            _fail("supply_chain_metadata_too_large")
        return path.read_bytes()
    except SupplyChainError:
        raise
    except OSError:
        _fail("supply_chain_metadata_invalid")


def _is_reparse_point(path: Path) -> bool:
    """Recognise both portable symlinks and Windows junction/reparse points."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        _fail("supply_chain_metadata_invalid")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_small_bytes(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("supply_chain_package_metadata_invalid")
    if not isinstance(value, dict):
        _fail("supply_chain_package_metadata_invalid")
    return value


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(value, encoding="utf-8", newline="\n")
    except OSError:
        _fail("supply_chain_output_write_failed")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _npm_purl(name: str, version: str) -> str:
    if name.startswith("@") and "/" in name:
        scope, package = name.split("/", 1)
        encoded = f"{quote(scope, safe='')}/{quote(package, safe='')}"
    else:
        encoded = quote(name, safe="")
    return f"pkg:npm/{encoded}@{quote(version, safe='.-_+')}"


def _python_purl(name: str, version: str) -> str:
    normalized = _canonical_name(name)
    return f"pkg:pypi/{quote(normalized, safe='-')}@{quote(version, safe='.-_+')}"


def _license_candidates(directory: Path) -> list[Path]:
    try:
        candidates: list[Path] = []
        for path in directory.iterdir():
            if not path.name.lower().startswith(_LICENSE_PREFIXES):
                continue
            if _is_reparse_point(path):
                _fail("supply_chain_dependency_tree_reparse_unsupported")
            if path.is_file():
                candidates.append(path)
        return sorted(candidates, key=lambda path: path.name.lower())
    except OSError:
        _fail("supply_chain_dependency_tree_unreadable")


@dataclass(frozen=True)
class LicenseText:
    sha256: str
    text: str
    source_name: str


@dataclass(frozen=True)
class ComponentRecord:
    ecosystem: str
    name: str
    version: str
    purl: str
    declared_license: str | None
    metadata_sha256: str
    instance_count: int
    license_texts: tuple[LicenseText, ...]
    review_codes: tuple[str, ...]
    concluded_license: str | None = None
    reviewed_evidence: Mapping[str, Any] | None = None

    @property
    def bom_ref(self) -> str:
        return self.purl


def _read_license_text(path: Path) -> LicenseText:
    raw = _read_small_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail("supply_chain_license_text_not_utf8")
    return LicenseText(
        sha256=_sha256_bytes(raw),
        text=text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n",
        source_name=path.name,
    )


@dataclass(frozen=True)
class _PnpmPackageConstraint:
    os: tuple[str, ...] = ()
    cpu: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PnpmSnapshot:
    dependencies: tuple[tuple[str, str], ...]
    optional_dependencies: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _PnpmLockGraph:
    root_dependencies: tuple[tuple[str, str], ...]
    root_optional_dependencies: tuple[tuple[str, str], ...]
    packages: Mapping[str, _PnpmPackageConstraint]
    snapshots: Mapping[str, _PnpmSnapshot]


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        _fail("supply_chain_pnpm_lock_invalid")
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            _fail("supply_chain_pnpm_lock_invalid")
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            _fail("supply_chain_pnpm_lock_invalid")
        if not isinstance(decoded, str):
            _fail("supply_chain_pnpm_lock_invalid")
        return decoded
    if value[0] in "!&*|>{[" or " #" in value:
        _fail("supply_chain_pnpm_lock_invalid")
    return value


def _yaml_mapping_item(line: str, indent: int) -> tuple[str, str]:
    if len(line) - len(line.lstrip(" ")) != indent:
        _fail("supply_chain_pnpm_lock_invalid")
    value = line[indent:]
    quote_char: str | None = None
    escaped = False
    separator = -1
    for index, character in enumerate(value):
        if quote_char == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quote_char = None
            continue
        if quote_char == "'":
            if character == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    continue
                quote_char = None
            continue
        if character in "'\"":
            quote_char = character
        elif character == ":" and (index + 1 == len(value) or value[index + 1].isspace()):
            separator = index
            break
    if separator <= 0 or quote_char is not None:
        _fail("supply_chain_pnpm_lock_invalid")
    return _yaml_scalar(value[:separator]), value[separator + 1 :].strip()


def _yaml_flow_strings(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value.startswith("[") or not value.endswith("]"):
        _fail("supply_chain_pnpm_lock_invalid")
    body = value[1:-1].strip()
    if not body:
        return ()
    return tuple(_yaml_scalar(item) for item in body.split(","))


def _lock_section(lines: Sequence[str], section: str) -> tuple[str, ...]:
    marker = f"{section}:"
    positions = [index for index, line in enumerate(lines) if line == marker]
    if len(positions) != 1:
        _fail("supply_chain_pnpm_lock_invalid")
    start = positions[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index] and not lines[index].startswith(" "):
            end = index
            break
    return tuple(lines[start:end])


def _parse_pnpm_lock(lockfile: Path) -> _PnpmLockGraph:
    """Parse the narrow deterministic YAML subset emitted by pnpm lock v9."""

    try:
        lines = _read_small_bytes(lockfile).decode("utf-8").splitlines()
    except UnicodeError:
        _fail("supply_chain_pnpm_lock_invalid")
    if any("\t" in line for line in lines):
        _fail("supply_chain_pnpm_lock_invalid")
    version_lines = [line for line in lines if line.startswith("lockfileVersion:")]
    if len(version_lines) != 1:
        _fail("supply_chain_pnpm_lock_invalid")
    _, raw_version = _yaml_mapping_item(version_lines[0], 0)
    if _yaml_scalar(raw_version) != "9.0":
        _fail("supply_chain_pnpm_lock_version_unsupported")

    package_lines = _lock_section(lines, "packages")
    packages: dict[str, _PnpmPackageConstraint] = {}
    current_package: str | None = None
    package_os: tuple[str, ...] = ()
    package_cpu: tuple[str, ...] = ()

    def finish_package() -> None:
        if current_package is not None:
            packages[current_package] = _PnpmPackageConstraint(package_os, package_cpu)

    for line in package_lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2:
            finish_package()
            key, _ = _yaml_mapping_item(line, 2)
            if key in packages:
                _fail("supply_chain_pnpm_lock_invalid")
            current_package = key
            package_os = ()
            package_cpu = ()
        elif indent == 4 and current_package is not None:
            key, value = _yaml_mapping_item(line, 4)
            if key == "os":
                package_os = _yaml_flow_strings(value)
            elif key == "cpu":
                package_cpu = _yaml_flow_strings(value)
    finish_package()
    if not packages:
        _fail("supply_chain_pnpm_lock_invalid")

    snapshot_lines = _lock_section(lines, "snapshots")
    snapshots: dict[str, _PnpmSnapshot] = {}
    current_snapshot: str | None = None
    current_group: str | None = None
    dependencies: list[tuple[str, str]] = []
    optional_dependencies: list[tuple[str, str]] = []
    awaiting_explicit_value = False

    def finish_snapshot() -> None:
        if current_snapshot is not None:
            snapshots[current_snapshot] = _PnpmSnapshot(
                tuple(dependencies),
                tuple(optional_dependencies),
            )

    for line in snapshot_lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2:
            stripped = line[2:]
            if stripped.startswith("? "):
                finish_snapshot()
                key = _yaml_scalar(stripped[2:])
                if key in snapshots:
                    _fail("supply_chain_pnpm_lock_invalid")
                current_snapshot = key
                current_group = None
                dependencies = []
                optional_dependencies = []
                awaiting_explicit_value = True
            elif stripped.startswith(": ") and awaiting_explicit_value:
                inline_group = stripped[2:]
                if not inline_group.endswith(":"):
                    _fail("supply_chain_pnpm_lock_invalid")
                key = inline_group[:-1]
                current_group = (
                    key if key in {"dependencies", "optionalDependencies"} else None
                )
                awaiting_explicit_value = False
            else:
                finish_snapshot()
                key, _ = _yaml_mapping_item(line, 2)
                if key in snapshots:
                    _fail("supply_chain_pnpm_lock_invalid")
                current_snapshot = key
                current_group = None
                dependencies = []
                optional_dependencies = []
                awaiting_explicit_value = False
        elif indent == 4 and current_snapshot is not None:
            key, _ = _yaml_mapping_item(line, 4)
            current_group = key if key in {"dependencies", "optionalDependencies"} else None
        elif indent == 6 and current_group is not None:
            name, raw_reference = _yaml_mapping_item(line, 6)
            reference = _yaml_scalar(raw_reference)
            target = dependencies if current_group == "dependencies" else optional_dependencies
            if any(existing == name for existing, _ in target):
                _fail("supply_chain_pnpm_lock_invalid")
            target.append((name, reference))
    finish_snapshot()
    if not snapshots:
        _fail("supply_chain_pnpm_lock_invalid")

    importer_lines = _lock_section(lines, "importers")
    in_root = False
    current_group = None
    current_dependency: tuple[str, str] | None = None
    root_dependencies: dict[str, str] = {}
    root_optional: dict[str, str] = {}
    for line in importer_lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2:
            key, _ = _yaml_mapping_item(line, 2)
            in_root = key == "."
            current_group = None
            current_dependency = None
        elif indent == 4 and in_root:
            key, _ = _yaml_mapping_item(line, 4)
            current_group = key if key in {"dependencies", "optionalDependencies"} else None
            current_dependency = None
        elif indent == 6 and in_root and current_group is not None:
            name, inline = _yaml_mapping_item(line, 6)
            current_dependency = (current_group, name)
            if inline and inline != "{}":
                target = root_dependencies if current_group == "dependencies" else root_optional
                target[name] = _yaml_scalar(inline)
        elif indent == 8 and in_root and current_dependency is not None:
            key, raw_value = _yaml_mapping_item(line, 8)
            if key == "version":
                group, name = current_dependency
                target = root_dependencies if group == "dependencies" else root_optional
                if name in target:
                    _fail("supply_chain_pnpm_lock_invalid")
                target[name] = _yaml_scalar(raw_value)
    if not root_dependencies:
        _fail("supply_chain_pnpm_lock_invalid")
    return _PnpmLockGraph(
        tuple(sorted(root_dependencies.items())),
        tuple(sorted(root_optional.items())),
        packages,
        snapshots,
    )


def _pnpm_key_identity(key: str) -> tuple[str, str]:
    if key.startswith("@"):
        slash = key.find("/")
        separator = key.find("@", slash + 1)
    else:
        separator = key.find("@")
    if separator <= 0:
        _fail("supply_chain_pnpm_lock_invalid")
    name = key[:separator]
    version = key[separator + 1 :].split("(", 1)[0]
    if not version:
        _fail("supply_chain_pnpm_lock_invalid")
    return name, version


def _pnpm_dependency_context(name: str, reference: str) -> str:
    if reference.startswith("npm:"):
        context = reference[4:]
    elif reference.startswith(("link:", "file:", "workspace:")):
        _fail("supply_chain_pnpm_link_dependency_unsupported")
    else:
        context = f"{name}@{reference}"
    _pnpm_key_identity(context)
    return context


def _pnpm_target_platform() -> tuple[str, str]:
    os_name = {"win32": "win32", "darwin": "darwin", "linux": "linux"}.get(sys.platform)
    cpu_name = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "i386": "ia32",
        "i686": "ia32",
        "x86": "ia32",
    }.get(platform.machine().lower())
    if os_name is None or cpu_name is None:
        _fail("supply_chain_pnpm_platform_unsupported")
    return os_name, cpu_name


def _pnpm_constraint_matches(values: Sequence[str], target: str) -> bool:
    positives = {value for value in values if not value.startswith("!")}
    negatives = {value[1:] for value in values if value.startswith("!")}
    return target not in negatives and (not positives or target in positives)


def _pnpm_production_contexts(
    graph: _PnpmLockGraph,
    *,
    target_os: str,
    target_cpu: str,
) -> frozenset[str]:
    def optional_is_compatible(context: str) -> bool:
        identity = "@".join(_pnpm_key_identity(context))
        constraint = graph.packages.get(identity)
        if constraint is None:
            _fail("supply_chain_pnpm_lock_invalid")
        return _pnpm_constraint_matches(
            constraint.os,
            target_os,
        ) and _pnpm_constraint_matches(constraint.cpu, target_cpu)

    pending = [
        _pnpm_dependency_context(name, reference)
        for name, reference in graph.root_dependencies
    ]
    pending.extend(
        context
        for name, reference in graph.root_optional_dependencies
        if optional_is_compatible(
            context := _pnpm_dependency_context(name, reference),
        )
    )
    selected: set[str] = set()
    while pending:
        context = pending.pop()
        if context in selected:
            continue
        snapshot = graph.snapshots.get(context)
        identity = "@".join(_pnpm_key_identity(context))
        if snapshot is None or identity not in graph.packages:
            _fail("supply_chain_pnpm_lock_invalid")
        selected.add(context)
        pending.extend(
            _pnpm_dependency_context(name, reference)
            for name, reference in snapshot.dependencies
        )
        for name, reference in snapshot.optional_dependencies:
            optional_context = _pnpm_dependency_context(name, reference)
            if optional_is_compatible(optional_context):
                pending.append(optional_context)
    return frozenset(selected)


def _physical_package_roots(modules_root: Path) -> list[Path]:
    """Walk only Node's package slots, excluding examples with package.json."""

    found: list[Path] = []
    pending = [modules_root]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if _is_reparse_point(current):
            _fail("supply_chain_dependency_tree_reparse_unsupported")
        marker = str(current.resolve(strict=False)).casefold()
        if marker in seen:
            _fail("supply_chain_dependency_tree_alias")
        seen.add(marker)
        try:
            entries = sorted(current.iterdir(), key=lambda path: path.name)
        except OSError:
            _fail("supply_chain_dependency_tree_unreadable")
        roots: list[Path] = []
        for entry in entries:
            if _is_reparse_point(entry):
                _fail("supply_chain_dependency_tree_reparse_unsupported")
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if entry.name.startswith("@"):
                try:
                    roots.extend(
                        sorted(
                            (
                                path
                                for path in entry.iterdir()
                                if path.is_dir()
                            ),
                            key=lambda path: path.name,
                        ),
                    )
                except OSError:
                    _fail("supply_chain_dependency_tree_unreadable")
            else:
                roots.append(entry)
        for package_root in roots:
            if _is_reparse_point(package_root):
                _fail("supply_chain_dependency_tree_reparse_unsupported")
            package_path = package_root / "package.json"
            if not package_path.is_file():
                continue
            found.append(package_root)
            nested = package_root / "node_modules"
            if nested.exists() and _is_reparse_point(nested):
                _fail("supply_chain_dependency_tree_reparse_unsupported")
            if nested.is_dir():
                pending.append(nested)
    return found


def _isolated_package_roots(store: Path) -> list[Path]:
    roots: list[Path] = []
    try:
        virtual_entries = sorted(
            (path for path in store.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.name,
        )
    except OSError:
        _fail("supply_chain_dependency_tree_unreadable")
    for virtual in virtual_entries:
        packages_root = virtual / "node_modules"
        if packages_root.is_dir() and not packages_root.is_symlink():
            roots.extend(_physical_package_roots(packages_root))
    return roots


def _hoisted_locations(node_modules: Path) -> dict[str, tuple[str, ...]]:
    state_path = node_modules / ".modules.yaml"
    if not state_path.is_file():
        _fail("supply_chain_pnpm_hoisted_layout_required")
    try:
        state = json.loads(_read_small_bytes(state_path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("supply_chain_pnpm_installation_metadata_invalid")
    if not isinstance(state, dict) or state.get("nodeLinker") != "hoisted":
        _fail("supply_chain_pnpm_hoisted_layout_required")
    if state.get("included") != {
        "dependencies": True,
        "devDependencies": False,
        "optionalDependencies": True,
    }:
        _fail("supply_chain_pnpm_installation_metadata_invalid")
    package_manager = _read_json(node_modules.parent / "package.json").get("packageManager")
    if not isinstance(package_manager, str) or state.get("packageManager") != package_manager:
        _fail("supply_chain_pnpm_installation_metadata_invalid")
    raw = state.get("hoistedLocations")
    if not isinstance(raw, dict) or not raw:
        _fail("supply_chain_pnpm_installation_metadata_invalid")
    result: dict[str, tuple[str, ...]] = {}
    for key, locations in raw.items():
        if (
            not isinstance(key, str)
            or not isinstance(locations, list)
            or not locations
            or not all(isinstance(value, str) and value for value in locations)
        ):
            _fail("supply_chain_pnpm_installation_metadata_invalid")
        result[key] = tuple(locations)
    return result


def inventory_pnpm_packages(
    node_modules: Path,
    pnpm_lock: Path,
) -> tuple[ComponentRecord, ...]:
    """Inventory the exact platform production graph from a hoisted install.

    pnpm's isolated linker is intentionally not accepted for this Windows
    release line.  The source and installed lockfiles, lock-v9 dependency
    closure, hoisted peer-context map, and physical package slots must all
    agree before any package metadata or licence text is trusted.
    """

    store = node_modules / ".pnpm"
    if _is_reparse_point(node_modules) or not node_modules.is_dir():
        _fail("supply_chain_pnpm_store_missing")
    if not store.is_dir() or _is_reparse_point(store):
        _fail("supply_chain_pnpm_store_missing")
    installed_lock = store / "lock.yaml"
    if _read_small_bytes(pnpm_lock) != _read_small_bytes(installed_lock):
        _fail("supply_chain_pnpm_installation_lock_mismatch")
    graph = _parse_pnpm_lock(pnpm_lock)
    target_os, target_cpu = _pnpm_target_platform()
    selected_contexts = _pnpm_production_contexts(
        graph,
        target_os=target_os,
        target_cpu=target_cpu,
    )
    locked_identities = {
        _pnpm_key_identity(context)
        for context in selected_contexts
    }
    locations = _hoisted_locations(node_modules)
    if set(locations) != selected_contexts:
        _fail("supply_chain_pnpm_lock_installation_mismatch")
    package_roots = _physical_package_roots(node_modules)
    expected_paths: dict[str, tuple[str, str]] = {}
    project_root = node_modules.parent.resolve()
    modules_resolved = node_modules.resolve()
    for context, relative_locations in locations.items():
        identity = _pnpm_key_identity(context)
        for relative in relative_locations:
            normalized_relative = relative.replace("\\", "/")
            parts = normalized_relative.split("/")
            if (
                not parts
                or parts[0] != "node_modules"
                or any(not part or part in {".", ".."} for part in parts)
                or re.match(r"^[A-Za-z]:", normalized_relative)
            ):
                _fail("supply_chain_pnpm_installation_metadata_invalid")
            candidate = project_root.joinpath(*parts)
            cursor = project_root
            for part in parts:
                cursor /= part
                if cursor.exists() and _is_reparse_point(cursor):
                    _fail("supply_chain_dependency_tree_reparse_unsupported")
            try:
                normalized = candidate.resolve(strict=False).relative_to(
                    modules_resolved,
                ).as_posix().casefold()
            except ValueError:
                _fail("supply_chain_pnpm_installation_metadata_invalid")
            if normalized in expected_paths:
                _fail("supply_chain_pnpm_installation_metadata_invalid")
            expected_paths[normalized] = identity
    actual_by_path = {
        path.resolve().relative_to(modules_resolved).as_posix().casefold(): path
        for path in package_roots
    }
    if set(actual_by_path) != set(expected_paths):
        _fail("supply_chain_pnpm_lock_installation_mismatch")

    instances: dict[tuple[str, str], list[tuple[Path, dict[str, Any], str]]] = defaultdict(list)
    for normalized, package_root in sorted(actual_by_path.items()):
        package_path = package_root / "package.json"
        package = _read_json(package_path)
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            _fail("supply_chain_package_identity_invalid")
        identity = (name, version)
        if identity != expected_paths[normalized] or identity not in locked_identities:
            _fail("supply_chain_pnpm_lock_installation_mismatch")
        instances[identity].append(
            (package_root, package, _sha256_file(package_path)),
        )
    if not instances:
        _fail("supply_chain_pnpm_store_empty")
    if set(instances) != locked_identities:
        _fail("supply_chain_pnpm_lock_installation_mismatch")

    records: list[ComponentRecord] = []
    for (name, version), values in sorted(instances.items()):
        metadata_hashes = {value[2] for value in values}
        declarations = {
            value[1].get("license")
            for value in values
            if isinstance(value[1].get("license"), str) and value[1]["license"].strip()
        }
        if len(metadata_hashes) != 1 or len(declarations) > 1:
            _fail("supply_chain_peer_variant_metadata_mismatch")
        texts: dict[str, LicenseText] = {}
        for package_root, _, _ in values:
            for path in _license_candidates(package_root):
                license_text = _read_license_text(path)
                texts.setdefault(license_text.sha256, license_text)
        declared = next(iter(declarations), None)
        review: list[str] = []
        if declared is None:
            review.append("declared_license_missing")
        if not texts:
            review.append("license_text_missing")
        records.append(
            ComponentRecord(
                ecosystem="npm",
                name=name,
                version=version,
                purl=_npm_purl(name, version),
                declared_license=declared,
                metadata_sha256=next(iter(metadata_hashes)),
                instance_count=len(values),
                license_texts=tuple(texts[key] for key in sorted(texts)),
                review_codes=tuple(review),
            ),
        )
    return tuple(records)


def _python_license_paths(distribution: importlib.metadata.Distribution) -> list[Path]:
    paths: dict[str, Path] = {}
    for relative in distribution.files or ():
        if not relative.name.lower().startswith(_LICENSE_PREFIXES):
            continue
        located = Path(distribution.locate_file(relative))
        if located.is_file() and not located.is_symlink():
            paths[str(located.resolve())] = located
    return [paths[key] for key in sorted(paths)]


def inventory_python_packages(names: Sequence[str]) -> tuple[ComponentRecord, ...]:
    """Inventory explicit Python runtime distributions from local metadata."""

    records: list[ComponentRecord] = []
    seen: set[str] = set()
    for requested in sorted(names, key=_canonical_name):
        canonical = _canonical_name(requested)
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            distribution = importlib.metadata.distribution(requested)
        except importlib.metadata.PackageNotFoundError:
            _fail("supply_chain_python_distribution_missing")
        metadata = distribution.metadata
        name = metadata.get("Name") or requested
        version = distribution.version
        expression = metadata.get("License-Expression")
        legacy = metadata.get("License")
        declared = expression.strip() if expression and expression.strip() else None
        if declared is None and legacy and legacy.strip() and "\n" not in legacy.strip():
            declared = legacy.strip()
        texts: dict[str, LicenseText] = {}
        for path in _python_license_paths(distribution):
            text = _read_license_text(path)
            texts.setdefault(text.sha256, text)
        if not texts and legacy and "\n" in legacy:
            normalized = legacy.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
            raw = normalized.encode("utf-8")
            text = LicenseText(
                sha256=_sha256_bytes(raw),
                text=normalized,
                source_name="METADATA:License",
            )
            texts[text.sha256] = text
        metadata_text = "\n".join(f"{key}: {value}" for key, value in sorted(metadata.items()))
        review: list[str] = []
        if declared is None:
            review.append("declared_license_missing")
        if not texts:
            review.append("license_text_missing")
        records.append(
            ComponentRecord(
                ecosystem="pypi",
                name=name,
                version=version,
                purl=_python_purl(name, version),
                declared_license=declared,
                metadata_sha256=_sha256_bytes(metadata_text.encode("utf-8")),
                instance_count=1,
                license_texts=tuple(texts[key] for key in sorted(texts)),
                review_codes=tuple(review),
            ),
        )
    return tuple(sorted(records, key=lambda value: value.bom_ref))


def _runtime_python_distribution_names() -> tuple[str, ...]:
    """Resolve the Windows pywebview runtime dependency closure.

    The release currently targets Windows and the list is deliberately
    explicit.  A version/change of the GUI stack must update this function and
    its acceptance test instead of silently inheriting arbitrary build tools.
    """

    return (
        "bottle",
        "cffi",
        "clr-loader",
        "proxy_tools",
        "pycparser",
        "pyinstaller",
        "pythonnet",
        "pywebview",
        "typing_extensions",
    )


def _database_identity(database: Path) -> tuple[str, str]:
    try:
        with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'raw_upstream_commit'",
            ).fetchone()
    except sqlite3.Error:
        _fail("supply_chain_database_identity_unavailable")
    if row is None or not isinstance(row[0], str) or not re.fullmatch(r"[0-9a-f]{40}", row[0]):
        _fail("supply_chain_database_identity_unavailable")
    return row[0], _sha256_file(database)


def _component_json(record: ComponentRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "library",
        "bom-ref": record.bom_ref,
        "name": record.name,
        "version": record.version,
        "purl": record.purl,
        "properties": [
            {"name": "genshin:ecosystem", "value": record.ecosystem},
            {"name": "genshin:instance-count", "value": str(record.instance_count)},
            {"name": "genshin:package-metadata-sha256", "value": record.metadata_sha256},
            {
                "name": "genshin:license-review",
                "value": ",".join(record.review_codes) if record.review_codes else "clear",
            },
        ],
    }
    if record.concluded_license or record.declared_license:
        result["licenses"] = [{"license": {"name": record.concluded_license or record.declared_license}}]
    if record.reviewed_evidence:
        result["properties"].append({"name": "genshin:license-evidence", "value": LICENSE_REVIEW_FILENAME})
    return result


def _license_manifest_record(record: ComponentRecord) -> dict[str, Any]:
    return {
        "ecosystem": record.ecosystem,
        "name": record.name,
        "version": record.version,
        "purl": record.purl,
        "declared_license": record.declared_license,
        "concluded_license": record.concluded_license,
        "reviewed_evidence": record.reviewed_evidence,
        "package_metadata_sha256": record.metadata_sha256,
        "instance_count": record.instance_count,
        "license_files": [
            {
                "source_name": value.source_name,
                "sha256": value.sha256,
                "notice_section": f"license-{value.sha256}",
            }
            for value in record.license_texts
        ],
        "review_codes": list(record.review_codes),
    }


def _notices_text(
    records: Sequence[ComponentRecord],
    *,
    node_version: str,
    node_license: LicenseText | None,
    python_version: str,
    python_license: LicenseText | None,
    database_commit: str,
) -> str:
    owners: dict[str, set[str]] = defaultdict(set)
    texts: dict[str, LicenseText] = {}
    for record in records:
        for value in record.license_texts:
            texts[value.sha256] = value
            owners[value.sha256].add(f"{record.ecosystem}:{record.name}@{record.version}")
    if node_license is not None:
        texts[node_license.sha256] = node_license
        owners[node_license.sha256].add(f"runtime:Node.js@{node_version}")
    if python_license is not None:
        texts[python_license.sha256] = python_license
        owners[python_license.sha256].add(f"runtime:CPython@{python_version}")

    lines = [
        "GenshinCalc Third-Party Notices",
        "================================",
        "",
        "This file is generated offline from the exact installed release inputs.",
        "Package licence fields are upstream declarations, not a legal conclusion.",
        "Items marked REVIEW REQUIRED have no local licence text or declaration and",
        "must be reviewed before a formal release.",
        "",
        "Bundled game-data boundary",
        "--------------------------",
        f"The database derives from YuanShenResources commit {database_commit}.",
        "That upstream snapshot has no LICENSE or COPYING file. No open-source",
        "licence is asserted here; the concluded licence remains NOASSERTION.",
        "Legal, copyright, and redistribution assessment is user-managed and is",
        "not an automated engineering release gate under ADR-0015:",
        f"{DATABASE_ADR}. Provenance is recorded in {DATABASE_BASELINE}.",
        "",
        "Component declarations",
        "----------------------",
        f"runtime:Node.js@{node_version} | declared: NOASSERTION | "
        + ("text: included" if node_license else "REVIEW REQUIRED: licence text missing"),
        f"runtime:CPython@{python_version} | declared: PSF-2.0 | "
        + ("text: included" if python_license else "REVIEW REQUIRED: licence text missing"),
    ]
    for record in sorted(records, key=lambda value: value.bom_ref):
        declaration = record.declared_license or "NOASSERTION"
        review = ",".join(record.review_codes) if record.review_codes else "clear"
        lines.append(
            f"{record.ecosystem}:{record.name}@{record.version} | "
            f"declared: {declaration} | review: {review}",
        )
        if record.reviewed_evidence:
            lines.append(f"  Reviewed: {record.concluded_license}; {record.reviewed_evidence['review_note']}")
            for document in record.reviewed_evidence["documents"]:
                lines.append(f"  Source: {document['source_url']} [{document['notice_section']}]")
    lines.extend(["", "Licence texts", "-------------"])
    for digest in sorted(texts):
        value = texts[digest]
        lines.extend(
            [
                "",
                f"[license-{digest}]",
                f"Used by: {', '.join(sorted(owners[digest]))}",
                f"Source filename: {value.source_name}",
                "",
                value.text.rstrip(),
            ],
        )
    return "\n".join(lines).rstrip() + "\n"


def generate_supply_chain_materials(
    *,
    output_directory: Path,
    app_version: str,
    runtime_root: Path,
    node_executable: Path,
    node_version: str,
    database: Path,
    pnpm_lock: Path,
    runtime_manifest: Path,
    node_license_path: Path | None = None,
    python_license_path: Path | None = None,
    python_distribution_names: Sequence[str] | None = None,
    license_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Generate SBOM, licence manifest, notices, and a hashed receipt."""

    npm = inventory_pnpm_packages(runtime_root / "node_modules", pnpm_lock)
    python_names = python_distribution_names or _runtime_python_distribution_names()
    python = inventory_python_packages(python_names)
    records = tuple(sorted((*npm, *python), key=lambda value: value.bom_ref))
    database_commit, database_hash = _database_identity(database)
    node_hash = _sha256_file(node_executable)
    node_license = _read_license_text(node_license_path) if node_license_path else None
    if python_license_path is None:
        sibling = Path(sys.base_prefix) / "LICENSE.txt"
        python_license_path = sibling if sibling.is_file() else None
    python_license = _read_license_text(python_license_path) if python_license_path else None
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    reviews: dict[str, Any] = {}
    if license_evidence_path is not None:
        try:
            reviews = load_license_evidence(license_evidence_path)
            identities = {r.purl: {"package_metadata_sha256": r.metadata_sha256} for r in records}
            identities[f"pkg:generic/node.js@{node_version}"] = {"binary_sha256": node_hash}
            bind_license_evidence(reviews, identities)
        except LicenseEvidenceError as error:
            _fail(str(error))
        reviewed_records = []
        for record in records:
            entry = reviews.get(record.purl)
            if entry is not None:
                if set(record.review_codes) - set(entry["resolved_codes"]):
                    _fail(f"license_evidence_review_scope_mismatch:{record.purl}")
                texts = {value.sha256: value for value in record.license_texts}
                for doc in entry["documents"]:
                    texts[doc["sha256"]] = LicenseText(doc["sha256"], doc["text"], doc["path"])
                record = replace(
                    record, license_texts=tuple(texts[key] for key in sorted(texts)),
                    review_codes=(), concluded_license=entry["concluded_license"],
                    reviewed_evidence=public_review(entry),
                )
            reviewed_records.append(record)
        records = tuple(reviewed_records)
        node_review = reviews.get(f"pkg:generic/node.js@{node_version}")
        if node_review:
            doc = node_review["documents"][0]
            if node_license is not None and node_license.sha256 != doc["sha256"]:
                _fail("license_evidence_node_text_mismatch")
            node_license = LicenseText(doc["sha256"], doc["text"], doc["path"])

    root_ref = f"pkg:pypi/genshin-db@{quote(app_version, safe='.-_+')}"
    node_ref = f"pkg:generic/node.js@{quote(node_version, safe='.-_+')}"
    python_ref = f"pkg:generic/cpython@{python_version}"
    database_ref = f"pkg:generic/yuanshenresources@{database_commit}?download_url=gitlab.com%2FGuraFoundation%2FYuanShenResources"
    components = [_component_json(record) for record in records]
    components.extend(
        [
            {
                "type": "framework",
                "bom-ref": node_ref,
                "name": "Node.js",
                "version": node_version,
                "hashes": [{"alg": "SHA-256", "content": node_hash}],
                "properties": [
                    {"name": "genshin:license-review", "value": "clear" if node_license else "license_text_missing"},
                ],
            },
            {
                "type": "framework",
                "bom-ref": python_ref,
                "name": "CPython",
                "version": python_version,
                "licenses": [{"license": {"name": "PSF-2.0"}}],
                "properties": [
                    {"name": "genshin:license-review", "value": "clear" if python_license else "license_text_missing"},
                ],
            },
            {
                "type": "data",
                "bom-ref": database_ref,
                "name": "YuanShenResources-derived genshin.db",
                "version": database_commit,
                "hashes": [{"alg": "SHA-256", "content": database_hash}],
                "properties": [
                    {"name": "genshin:license-concluded", "value": "NOASSERTION"},
                    {"name": "genshin:legal-assessment", "value": "user-managed"},
                    {"name": "genshin:engineering-release-gate", "value": "not-applicable"},
                ],
            },
        ],
    )
    components.sort(key=lambda value: value["bom-ref"])
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "GenshinCalc",
                "version": app_version,
            },
            "properties": [
                {"name": "genshin:generator", "value": "release_supply_chain.py/v1"},
                {"name": "genshin:offline", "value": "true"},
                {"name": "genshin:pnpm-lock-sha256", "value": _sha256_file(pnpm_lock)},
                {"name": "genshin:runtime-manifest-sha256", "value": _sha256_file(runtime_manifest)},
            ],
        },
        "components": components,
        "dependencies": [
            {"ref": root_ref, "dependsOn": [value["bom-ref"] for value in components]},
        ],
    }
    review_items: list[dict[str, str]] = []
    if node_license is None:
        review_items.append({"component": node_ref, "code": "license_text_missing"})
    if python_license is None:
        review_items.append({"component": python_ref, "code": "license_text_missing"})
    for record in records:
        review_items.extend(
            {"component": record.bom_ref, "code": code}
            for code in record.review_codes
        )
    license_manifest = {
        "contract": "genshin-release-license-manifest-v2",
        "app_version": app_version,
        "offline": True,
        "database": {
            "name": "YuanShenResources-derived genshin.db",
            "upstream_commit": database_commit,
            "sha256": database_hash,
            "license_concluded": "NOASSERTION",
            "policy": "user_managed_legal_assessment",
            "engineering_release_gate": False,
            "adr": DATABASE_ADR,
            "baseline": DATABASE_BASELINE,
        },
        "runtimes": [
            {
                "name": "Node.js",
                "version": node_version,
                "sha256": node_hash,
                "declared_license": "NOASSERTION",
                "license_files": [] if node_license is None else [
                    {"sha256": node_license.sha256, "notice_section": f"license-{node_license.sha256}"},
                ],
            },
            {
                "name": "CPython",
                "version": python_version,
                "declared_license": "PSF-2.0",
                "license_files": [] if python_license is None else [
                    {"sha256": python_license.sha256, "notice_section": f"license-{python_license.sha256}"},
                ],
            },
        ],
        "components": [_license_manifest_record(record) for record in records],
        "review_required": review_items,
        "machine_complete": not review_items,
        "release_decision": "human_review_required" if review_items else "machine_inventory_complete",
        "reviewed_evidence": None if not reviews else {
            "file": LICENSE_REVIEW_FILENAME,
            "manifest_sha256": _sha256_file(license_evidence_path),
            "reviewed_components": len(reviews),
        },
    }
    notices = _notices_text(
        records,
        node_version=node_version,
        node_license=node_license,
        python_version=python_version,
        python_license=python_license,
        database_commit=database_commit,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    sbom_path = output_directory / SBOM_FILENAME
    license_path = output_directory / LICENSE_MANIFEST_FILENAME
    notices_path = output_directory / NOTICE_FILENAME
    _write_text(sbom_path, _json_text(sbom))
    _write_text(license_path, _json_text(license_manifest))
    _write_text(notices_path, notices)
    review_path = output_directory / LICENSE_REVIEW_FILENAME
    _write_text(review_path, _json_text({
        "contract": "genshin-release-license-review-v1",
        "evidence_manifest_sha256": _sha256_file(license_evidence_path) if license_evidence_path else None,
        "components": [public_review(reviews[key]) for key in sorted(reviews)],
        "remaining_items": review_items,
        "scope": "Version-bound third-party software materials; not a blanket legal warranty or game-data licence.",
    }))
    receipt = {
        "contract": "genshin-release-supply-chain-receipt-v1",
        "app_version": app_version,
        "component_counts": {
            "npm": len(npm),
            "python": len(python),
            "total": len(components),
        },
        "database_upstream_commit": database_commit,
        "database_sha256": database_hash,
        "node_sha256": node_hash,
        "machine_complete": not review_items,
        "review_required_count": len(review_items),
        "outputs": {
            SBOM_FILENAME: _sha256_file(sbom_path),
            LICENSE_MANIFEST_FILENAME: _sha256_file(license_path),
            NOTICE_FILENAME: _sha256_file(notices_path),
            LICENSE_REVIEW_FILENAME: _sha256_file(review_path),
        },
    }
    receipt_path = output_directory / SUPPLY_CHAIN_RECEIPT_FILENAME
    _write_text(receipt_path, _json_text(receipt))
    return receipt


def write_package_file_manifest(package_root: Path) -> dict[str, Any]:
    """Hash the actual portable package without following links."""

    try:
        root = package_root.resolve(strict=True)
    except OSError:
        _fail("supply_chain_package_root_missing")
    if not root.is_dir() or root.is_symlink():
        _fail("supply_chain_package_root_missing")
    rows: list[tuple[str, str, int]] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in names:
            if (current / name).is_symlink():
                _fail("supply_chain_package_link_unsupported")
        for name in files:
            path = current / name
            if path.is_symlink():
                _fail("supply_chain_package_link_unsupported")
            relative = path.relative_to(root).as_posix()
            if relative in _PACKAGE_MANIFEST_EXCLUDED_PATHS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                _fail("supply_chain_package_unreadable")
            rows.append((relative, _sha256_file(path), size))
    rows.sort(key=lambda value: value[0])
    manifest_text = "".join(f"{digest}  {relative}\n" for relative, digest, _ in rows)
    manifest_path = root / PACKAGE_MANIFEST_FILENAME
    _write_text(manifest_path, manifest_text)
    return {
        "contract": "genshin-package-file-manifest-v1",
        "definition": PACKAGE_MANIFEST_DEFINITION,
        "files": len(rows),
        "bytes": sum(value[2] for value in rows),
        "manifest_sha256": _sha256_file(manifest_path),
    }


def verify_package_file_manifest(package_root: Path) -> dict[str, Any]:
    try:
        return verify_package_artifact(package_root)
    except PackageIntegrityError as error:
        _fail(error.code)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线生成发行SBOM、许可证材料与包文件哈希")
    parser.add_argument("--output", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--runtime-root", default="agent-runtime")
    parser.add_argument("--node", required=True)
    parser.add_argument("--node-version", required=True)
    parser.add_argument("--node-license")
    parser.add_argument("--python-license")
    parser.add_argument("--license-evidence", default=str(DEFAULT_LICENSE_EVIDENCE) if DEFAULT_LICENSE_EVIDENCE.is_file() else None)
    parser.add_argument("--database", default="data/genshin.db")
    parser.add_argument("--pnpm-lock", default="agent-runtime/pnpm-lock.yaml")
    parser.add_argument(
        "--runtime-manifest",
        default=None,
    )
    parser.add_argument("--package-root")
    args = parser.parse_args(argv)
    if args.runtime_manifest is None:
        # The native release builder owns the accepted manifest filename.
        # Respect an alternate runtime root instead of duplicating a stale
        # composition generation in this independent maintenance command.
        try:
            from scripts.native_agent_release import RUNTIME_MANIFEST
        except ModuleNotFoundError as error:
            if error.name not in ("scripts", "scripts.native_agent_release"):
                raise
            from native_agent_release import RUNTIME_MANIFEST
        args.runtime_manifest = str(Path(args.runtime_root) / RUNTIME_MANIFEST)
    try:
        receipt = generate_supply_chain_materials(
            output_directory=Path(args.output),
            app_version=args.app_version,
            runtime_root=Path(args.runtime_root),
            node_executable=Path(args.node),
            node_version=args.node_version,
            node_license_path=Path(args.node_license) if args.node_license else None,
            python_license_path=Path(args.python_license) if args.python_license else None,
            license_evidence_path=Path(args.license_evidence) if args.license_evidence else None,
            database=Path(args.database),
            pnpm_lock=Path(args.pnpm_lock),
            runtime_manifest=Path(args.runtime_manifest),
        )
        if args.package_root:
            receipt["package"] = write_package_file_manifest(Path(args.package_root))
    except SupplyChainError as error:
        parser.error(error.code)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
