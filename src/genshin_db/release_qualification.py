"""Exact-byte verification for a database qualified by the release publisher."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from collections.abc import Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from genshin_db.release_profile import (
    DatabaseReleaseError,
    manifest_fingerprint,
    validate_database_release_manifest,
)

QUALIFICATION_CONTRACT = "genshin-database-install-qualification-v1"
_CHUNK_SIZE = 4 * 1024 * 1024
_SQLITE_HEADER = b"SQLite format 3\x00"
_SIDECARS = ("-wal", "-shm", "-journal")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True, slots=True)
class DatabaseInstallQualification:
    contract: str
    database_release_id: str
    manifest_fingerprint: str
    sha256: str
    size_bytes: int
    release_core_sha256: str
    meta_fingerprint: str
    mutable_layer_fingerprint: str
    manual_content_fingerprint: str | None

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def load_database_install_qualification(
    payload: Mapping[str, Any], manifest: Mapping[str, Any]
) -> DatabaseInstallQualification:
    """Check the bindings of a qualification in an already authenticated envelope.

    This function does not authenticate a local or downloaded document.
    """

    if not isinstance(payload, Mapping) or set(payload) != set(
        DatabaseInstallQualification.__dataclass_fields__
    ):
        raise DatabaseReleaseError("database_install_qualification_invalid")
    qualification = DatabaseInstallQualification(**payload)
    qualified_database_identity(manifest, qualification)
    return qualification


def qualified_database_identity(
    manifest: Mapping[str, Any], qualification: DatabaseInstallQualification
) -> dict[str, Any]:
    """Project the signed identity, without claiming any local path has these bytes."""

    checked = validate_database_release_manifest(dict(manifest))
    database = checked["database"]
    if (
        type(qualification) is not DatabaseInstallQualification
        or qualification.contract != QUALIFICATION_CONTRACT
        or qualification.database_release_id != checked["database_release_id"]
        or qualification.manifest_fingerprint != manifest_fingerprint(checked)
        or qualification.sha256 != database["sha256"]
        or type(qualification.size_bytes) is not int
        or qualification.size_bytes != database["size_bytes"]
        or qualification.release_core_sha256 != database["release_core"]["root_sha256"]
        or qualification.meta_fingerprint != database["meta_fingerprint"]
        or not _hex_sha256(qualification.mutable_layer_fingerprint)
        or (
            qualification.manual_content_fingerprint is not None
            and not _hex_sha256(qualification.manual_content_fingerprint)
        )
    ):
        raise DatabaseReleaseError("database_install_qualification_mismatch")
    return {
        **database,
        "release_core_root": qualification.release_core_sha256,
        "mutable_layer_fingerprint": qualification.mutable_layer_fingerprint,
        "manual_content_fingerprint": qualification.manual_content_fingerprint,
    }


def _hex_sha256(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _check_direct_path(path: Path, *, directory: bool = False) -> os.stat_result:
    # resolve() would hide a symlink or junction in an ancestor.
    for parent in reversed(path.parents):
        status = parent.lstat()
        if not stat.S_ISDIR(status.st_mode) or (
            getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise DatabaseReleaseError("database_install_qualified_path_unsafe")
    status = path.lstat()
    expected = stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)
    if (
        not expected
        or getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
        or (not directory and status.st_nlink != 1)
    ):
        raise DatabaseReleaseError("database_install_qualified_path_unsafe")
    return status


def _check_no_sidecars(path: Path) -> None:
    if any(os.path.lexists(f"{path}{suffix}") for suffix in _SIDECARS):
        raise DatabaseReleaseError("database_install_qualified_sidecar_present")


def check_qualified_destination(directory: Path) -> None:
    """Reject inherited junctions or links before creating a transaction path."""

    ancestor = Path(os.path.abspath(directory))
    while not os.path.lexists(ancestor):
        if ancestor == ancestor.parent:
            raise DatabaseReleaseError("database_install_qualified_path_unsafe")
        ancestor = ancestor.parent
    try:
        _check_direct_path(ancestor, directory=True)
    except OSError as error:
        raise DatabaseReleaseError("database_install_qualified_path_unsafe") from error


def _open_windows_read_only(path: Path) -> BinaryIO:
    # Deny write and delete sharing while both SHA-256 and SQLite read the file.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    handle = create(
        str(path), 0x80000000, 1, None, 3, 0x00200000 | 0x08000000, None
    )  # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, OPEN_REPARSE_POINT, SEQUENTIAL_SCAN
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close_handle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _locked_database_file(path: Path, *, size: int):
    before = _check_direct_path(path)
    _check_no_sidecars(path)
    if before.st_size != size:
        raise DatabaseReleaseError("database_install_qualified_bytes_mismatch")
    stream = _open_windows_read_only(path) if os.name == "nt" else os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb"
    )
    with stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened):
            raise DatabaseReleaseError("database_install_qualified_path_changed")
        yield stream
        after = os.fstat(stream.fileno())
        final_path = _check_direct_path(path)
        _check_no_sidecars(path)
        if (
            not os.path.samestat(after, final_path)
            or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (final_path.st_size, final_path.st_mtime_ns, final_path.st_ctime_ns)
        ):
            raise DatabaseReleaseError("database_install_qualified_path_changed")


def _read_bytes(
    stream: BinaryIO, *, size: int, sha256: str,
    output: BinaryIO | None = None,
) -> None:
    header = stream.read(20)
    if len(header) != 20 or not header.startswith(_SQLITE_HEADER) or header[18:20] != b"\x01\x01":
        raise DatabaseReleaseError("database_install_qualified_sqlite_header_invalid")
    digest = hashlib.sha256(header)
    remaining = size - len(header)
    if output is not None:
        output.write(header)
    while remaining > 0:
        block = stream.read(min(remaining, _CHUNK_SIZE))
        if not block:
            break
        digest.update(block)
        if output is not None:
            output.write(block)
        remaining -= len(block)
    if remaining != 0 or stream.read(1) or digest.hexdigest() != sha256:
        raise DatabaseReleaseError("database_install_qualified_bytes_mismatch")


@contextmanager
def hold_exact_database_bytes(path: Path, *, size: int, sha256: str):
    """Keep the verified file pinned during a subsequent read-only operation."""

    path = Path(os.path.abspath(path))
    try:
        with _locked_database_file(path, size=size) as stream:
            _read_bytes(stream, size=size, sha256=sha256)
            yield path
    except (OSError, sqlite3.Error) as error:
        raise DatabaseReleaseError("database_install_qualified_unreadable") from error


def _probe_read_only_sqlite(path: Path, identity: Mapping[str, Any]) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        meta = dict(connection.execute(
            "SELECT key, value FROM meta WHERE key IN "
            "('upstream_commit:release','upstream_commit','game_version:release','game_version')"
        ))
        if (
            (meta.get("upstream_commit:release") or meta.get("upstream_commit"))
            != identity["upstream_commit"]
            or (meta.get("game_version:release") or meta.get("game_version"))
            != identity["game_version"]
            or connection.execute(
                "SELECT id FROM character WHERE version_line='release' LIMIT 1"
            ).fetchone() is None
        ):
            raise DatabaseReleaseError("database_install_qualified_sqlite_probe_failed")


def read_qualified_database_identity(
    path: Path, manifest: Mapping[str, Any], qualification: DatabaseInstallQualification
) -> dict[str, Any]:
    """Verify the bytes at this exact path and perform an independent SQLite read."""

    identity = qualified_database_identity(manifest, qualification)
    with hold_exact_database_bytes(
        path, size=qualification.size_bytes, sha256=qualification.sha256,
    ) as verified_path:
        _probe_read_only_sqlite(verified_path, identity)
    return identity


def copy_qualified_database(
    source: Path, destination: Path, qualification: DatabaseInstallQualification
) -> None:
    """Create a durable independent snapshot while verifying its signed source bytes."""

    source = Path(os.path.abspath(source))
    destination = Path(os.path.abspath(destination))
    try:
        _check_direct_path(destination.parent, directory=True)
        with (
            _locked_database_file(source, size=qualification.size_bytes) as stream,
            destination.open("xb") as output,
        ):
            _read_bytes(
                stream, size=qualification.size_bytes, sha256=qualification.sha256,
                output=output,
            )
            output.flush()
            os.fsync(output.fileno())
            if not os.path.samestat(
                os.fstat(output.fileno()), _check_direct_path(destination)
            ):
                raise DatabaseReleaseError("database_install_qualified_path_changed")
    except OSError as error:
        raise DatabaseReleaseError("database_install_qualified_copy_failed") from error
