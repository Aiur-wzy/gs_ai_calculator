"""Read-only verification of the portable App artifact inventory.

The release pair is a detached descriptor and therefore cannot be part of the
content inventory whose digest it carries.  Every other regular file is listed
exactly once.  Formal startup currently performs the complete rehash as the
safe default; the descriptor-only check exists for a future signed/cached
startup policy and is not, by itself, an artifact-integrity trust decision.
"""

from __future__ import annotations

import hashlib
import os
import re
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
_ROW = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")


class PackageIntegrityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise PackageIntegrityError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        _fail("supply_chain_package_unreadable")
    return digest.hexdigest()


def _hash_package_member(row: tuple[str, Path, int]) -> tuple[str, str, int]:
    relative, path, size = row
    digest = _sha256_file(path)
    if _is_reparse_point(path) or not path.is_file():
        _fail("supply_chain_package_link_unsupported")
    return relative, digest, size


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

    This is only the bounded descriptor parser.  It does not prove that the
    listed package members still match their digests and must not replace the
    complete verifier until a separately reviewed signature/cache trust model
    has been adopted.
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
    """Fully rehash and compare every packaged file.

    This remains the formal startup default as well as the build/install/
    upgrade verifier.  Callers may move the full scan out of cold start only
    after adopting an explicit signed or verified-cache policy.
    """

    root, raw, expected = _read_package_manifest(package_root)
    actual: dict[str, tuple[str, int]] = {}
    actual_folded: set[str] = set()
    members: list[tuple[str, Path, int]] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if current != root and _is_reparse_point(current):
            _fail("supply_chain_package_link_unsupported")
        for name in names:
            if _is_reparse_point(current / name):
                _fail("supply_chain_package_link_unsupported")
        for name in files:
            path = current / name
            if _is_reparse_point(path):
                _fail("supply_chain_package_link_unsupported")
            relative = path.relative_to(root).as_posix()
            if relative in PACKAGE_MANIFEST_EXCLUDED_PATHS:
                continue
            if not path.is_file():
                _fail("supply_chain_package_unreadable")
            folded = relative.casefold()
            if relative in actual or folded in actual_folded:
                _fail("supply_chain_package_file_duplicate")
            try:
                size = path.stat().st_size
            except OSError:
                _fail("supply_chain_package_unreadable")
            members.append((relative, path, size))
            actual_folded.add(folded)

    # The release carries tens of thousands of small Node dependency files.
    # Hashing them serially turns a cold-start diagnostic into a multi-minute
    # operation on Windows.  Parallel reads preserve the exact same complete
    # file-set and digest contract while keeping build/install verification
    # bounded on SSD-backed installations.
    workers = min(32, max(8, (os.cpu_count() or 4) * 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for relative, digest, size in executor.map(_hash_package_member, members):
            actual[relative] = (digest, size)

    expected_paths = set(expected)
    actual_paths = set(actual)
    if expected_paths - actual_paths:
        _fail("supply_chain_package_manifest_file_missing")
    if actual_paths - expected_paths:
        _fail("supply_chain_package_manifest_file_unlisted")
    if any(actual[path][0] != expected[path] for path in expected):
        _fail("supply_chain_package_manifest_hash_mismatch")
    return {
        "contract": "genshin-package-file-manifest-verification-v1",
        "definition": PACKAGE_MANIFEST_DEFINITION,
        "status": "verified",
        "files": len(actual),
        "bytes": sum(value[1] for value in actual.values()),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }
