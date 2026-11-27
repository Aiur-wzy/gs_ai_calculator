"""Read-only verification of the portable App artifact inventory.

The release pair is a detached descriptor and therefore cannot be part of the
content inventory whose digest it carries.  Every other regular file is listed
exactly once. Installs, recovery, and portable startup verify the whole tree.
A signed product startup may defer the private DSH dependency directory to
its own pre-launch verifier. A descriptor alone never proves file integrity.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath

PACKAGE_MANIFEST_FILENAME = "PACKAGE-FILES.sha256"
DETACHED_PAIR_MANIFEST_PATH = "_internal/bundled/release-pair-manifest.json"
PACKAGE_MANIFEST_EXCLUDED_PATHS = frozenset(
    {PACKAGE_MANIFEST_FILENAME, DETACHED_PAIR_MANIFEST_PATH}
)
PACKAGE_MANIFEST_DEFINITION = (
    "all regular files below package_root except PACKAGE-FILES.sha256 "
    "and the detached release-pair manifest"
)
MAX_PACKAGE_MANIFEST_BYTES = 128 * 1024 * 1024
_DSH_DEPENDENCIES = "_internal/agent-runtime/node_modules"
_ROW = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")


class PackageIntegrityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise PackageIntegrityError(code)


def _linked(status: os.stat_result) -> bool:
    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _hash_package_batch(members: list[tuple[str, Path]]) -> list[tuple[str, str, int]]:
    buffer = memoryview(bytearray(128 * 1024))
    verified = []
    try:
        for relative, path in members:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                while count := stream.readinto(buffer):
                    digest.update(buffer[:count])
                current = path.lstat()
                if _linked(current) or not stat.S_ISREG(current.st_mode):
                    _fail("supply_chain_package_link_unsupported")
                if (
                    not os.path.samestat(opened, current)
                    or opened.st_size != current.st_size
                    or opened.st_mtime_ns != current.st_mtime_ns
                ):
                    _fail("supply_chain_package_unreadable")
            verified.append((relative, digest.hexdigest(), current.st_size))
    except OSError:
        _fail("supply_chain_package_unreadable")
    return verified


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        _fail("supply_chain_package_unreadable")


def _relative_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value in PACKAGE_MANIFEST_EXCLUDED_PATHS
        or value.startswith("/")
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        _fail("supply_chain_package_manifest_path_invalid")
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail("supply_chain_package_manifest_path_invalid")
    if PurePosixPath(value).as_posix() != value:
        _fail("supply_chain_package_manifest_path_invalid")
    return value


def _read_package_manifest(package_root: Path) -> tuple[Path, bytes, dict[str, str]]:
    supplied = Path(package_root)
    try:
        if _is_reparse_point(supplied) or not supplied.exists():
            _fail("supply_chain_package_root_missing")
        root = supplied.resolve(strict=True)
    except PackageIntegrityError:
        raise
    except OSError:
        _fail("supply_chain_package_root_missing")
    if not root.is_dir() or _is_reparse_point(root):
        _fail("supply_chain_package_root_missing")

    manifest_path = root / PACKAGE_MANIFEST_FILENAME
    if not manifest_path.is_file() or _is_reparse_point(manifest_path):
        _fail("supply_chain_package_manifest_missing")
    try:
        if manifest_path.stat().st_size > MAX_PACKAGE_MANIFEST_BYTES:
            _fail("supply_chain_package_manifest_too_large")
        raw = manifest_path.read_bytes()
        text = raw.decode("utf-8")
    except PackageIntegrityError:
        raise
    except (OSError, UnicodeError):
        _fail("supply_chain_package_manifest_unreadable")
    if b"\r" in raw or (text and not text.endswith("\n")):
        _fail("supply_chain_package_manifest_malformed")

    expected: dict[str, str] = {}
    expected_folded: set[str] = set()
    previous: str | None = None
    for line in text.splitlines():
        match = _ROW.fullmatch(line)
        if match is None:
            _fail("supply_chain_package_manifest_malformed")
        digest, relative = match.groups()
        relative = _relative_path(relative)
        folded = relative.casefold()
        if relative in expected or folded in expected_folded:
            _fail("supply_chain_package_manifest_duplicate")
        if previous is not None and relative <= previous:
            _fail("supply_chain_package_manifest_unsorted")
        expected[relative] = digest
        expected_folded.add(folded)
        previous = relative

    return root, raw, expected


def verify_package_manifest_descriptor(package_root: Path) -> dict:
    """Validate the detached inventory descriptor without rehashing the package.

    This is only the bounded descriptor parser. It does not check the bytes
    of the listed files.
    """

    _root, raw, expected = _read_package_manifest(package_root)
    return {
        "contract": "genshin-package-file-manifest-descriptor-v1",
        "definition": PACKAGE_MANIFEST_DEFINITION,
        "status": "descriptor_verified",
        "files": len(expected),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def verify_package_artifact(package_root: Path) -> dict:
    """Fully rehash and compare every packaged file."""
    return _verify_package_members(*_read_package_manifest(package_root))


def verify_signed_package_startup(package_root: Path, manifest_sha256: str) -> dict:
    """Verify startup members against an inventory from an authenticated pair.

    Only DSH's private Node dependencies are deferred. The production
    native DSH factory compares that whole dependency graph with its pinned
    runtime manifest before starting Node. The root of the deferred directory
    and every other package member are still checked here.
    """
    root, raw, expected = _read_package_manifest(package_root)
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        _fail("release_pair_app_artifact_mismatch")
    return _verify_package_members(root, raw, expected, defer_dsh_dependencies=True)


def read_signed_package_root_file(
    package_root: Path, manifest_sha256: str, filename: str, *, maximum_bytes: int,
) -> bytes | None:
    """Return an optional control file only if its actual bytes are authenticated."""
    if "/" in _relative_path(filename):
        _fail("supply_chain_package_manifest_path_invalid")
    if not os.path.lexists(Path(package_root) / filename):
        return None
    root, manifest, expected = _read_package_manifest(package_root)
    if hashlib.sha256(manifest).hexdigest() != manifest_sha256:
        _fail("release_pair_app_artifact_mismatch")
    if filename not in expected:
        _fail("supply_chain_package_manifest_file_unlisted")
    path = root / filename
    try:
        if _is_reparse_point(path) or not path.is_file():
            _fail("supply_chain_package_link_unsupported")
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
    except OSError:
        _fail("supply_chain_package_unreadable")
    if len(raw) > maximum_bytes or hashlib.sha256(raw).hexdigest() != expected[filename]:
        _fail("supply_chain_package_manifest_hash_mismatch")
    return raw


def _verify_package_members(
    root: Path,
    raw: bytes,
    expected: dict[str, str],
    *,
    defer_dsh_dependencies: bool = False,
) -> dict:
    deferred = 0
    if defer_dsh_dependencies:
        startup_members = {
            name: digest for name, digest in expected.items()
            if not name.startswith(f"{_DSH_DEPENDENCIES}/")
        }
        deferred = len(expected) - len(startup_members)
        expected = startup_members

    actual: dict[str, tuple[str, int]] = {}
    actual_folded: set[str] = set()
    members: list[tuple[str, Path]] = []
    deferred_directory_found = False
    pending = [(root, "")]
    try:
        while pending:
            current, prefix = pending.pop()
            if current != root and _linked(current.lstat()):
                _fail("supply_chain_package_link_unsupported")
            with os.scandir(current) as entries:
                for entry in entries:
                    relative = f"{prefix}/{entry.name}" if prefix else entry.name
                    status = entry.stat(follow_symlinks=False)
                    if _linked(status):
                        _fail("supply_chain_package_link_unsupported")
                    if stat.S_ISDIR(status.st_mode):
                        if defer_dsh_dependencies and relative == _DSH_DEPENDENCIES:
                            if not deferred:
                                _fail("supply_chain_package_manifest_file_unlisted")
                            deferred_directory_found = True
                        else:
                            pending.append((Path(entry.path), relative))
                        continue
                    if relative in PACKAGE_MANIFEST_EXCLUDED_PATHS:
                        continue
                    if not stat.S_ISREG(status.st_mode):
                        _fail("supply_chain_package_unreadable")
                    folded = relative.casefold()
                    if folded in actual_folded:
                        _fail("supply_chain_package_file_duplicate")
                    members.append((relative, Path(entry.path)))
                    actual_folded.add(folded)
    except OSError:
        _fail("supply_chain_package_unreadable")

    if deferred and not deferred_directory_found:
        _fail("supply_chain_package_manifest_file_missing")
    expected_paths = set(expected)
    actual_paths = {relative for relative, _path in members}
    if expected_paths - actual_paths:
        _fail("supply_chain_package_manifest_file_missing")
    if actual_paths - expected_paths:
        _fail("supply_chain_package_manifest_file_unlisted")
    # Batching amortizes the task and buffer cost for thousands of small files.
    workers = min(16, max(4, os.cpu_count() or 4))
    batches = (members[index:index + 32] for index in range(0, len(members), 32))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for verified in executor.map(_hash_package_batch, batches):
            for relative, digest, size in verified:
                actual[relative] = (digest, size)
    if any(actual[path][0] != expected[path] for path in expected):
        _fail("supply_chain_package_manifest_hash_mismatch")
    result = {
        "contract": "genshin-package-file-manifest-verification-v1",
        "definition": PACKAGE_MANIFEST_DEFINITION,
        "status": "verified",
        "files": len(actual),
        "bytes": sum(value[1] for value in actual.values()),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if defer_dsh_dependencies:
        result.update(status="startup_verified", deferred_dsh_dependency_files=deferred)
    return result
