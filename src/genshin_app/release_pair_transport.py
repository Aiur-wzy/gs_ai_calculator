"""Bounded transport for a signed App/database release-pair document.

The module only downloads and stages artifacts.  It never changes the active
App or database.  A caller must present the exact discovery-document digest
the user approved; every artifact is then streamed with size and SHA-256
checks into a fresh, isolated staging tree.
"""

from __future__ import annotations

import copy
import ctypes
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPException, IncompleteRead
from itertools import groupby
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event, Lock
from typing import Any
from uuid import uuid4

from genshin_app.package_integrity import (
    MAX_PACKAGE_MANIFEST_BYTES,
    PACKAGE_MANIFEST_DEFINITION,
    PACKAGE_MANIFEST_EXCLUDED_PATHS,
    PACKAGE_MANIFEST_FILENAME,
    PackageIntegrityError,
    _read_package_manifest,
)
from genshin_app.release_pair_discovery import _validate_artifact

TRANSPORT_RECEIPT_CONTRACT = "genshin-release-pair-transport-receipt-v1"
TRANSPORT_RECEIPT_FILENAME = "release-pair-transport-receipt.json"
TRANSPORT_SCHEMA_VERSION = 1
_STAGING_OWNER = {"contract": "genshin-release-pair-transport-staging-v1"}
_STAGING_OWNER_FILENAME = "release-pair-transport-owner.json"
MAX_APP_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_APP_UNPACKED_BYTES = 16 * 1024 * 1024 * 1024
MAX_APP_MEMBERS = 100_000
CHUNK_BYTES = 4 * 1024 * 1024
_SIGNED_APP_EXTRACTION_WORKERS = 4
_MAX_APP_EXTRACTION_WORKERS = 4

CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class StagedAppVerification:
    """Verification of a fresh directory in this install process only.

    A same-volume rename preserves the verified directory's identity. Do not
    persist this object or use it for an external helper, recovery, or a copy.
    """

    device: int
    inode: int
    manifest_sha256: str
    files: int
    bytes: int

    def report_for(self, root: Path) -> dict[str, Any] | None:
        root = Path(root).absolute()
        if any(_is_reparse_point(path) for path in (root, *root.parents)):
            return None
        try:
            info = root.stat()
        except OSError:
            return None
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (self.device, self.inode):
            return None
        return {
            "contract": "genshin-package-file-manifest-verification-v1",
            "definition": PACKAGE_MANIFEST_DEFINITION,
            "status": "verified",
            "files": self.files,
            "bytes": self.bytes,
            "manifest_sha256": self.manifest_sha256,
        }


class ReleasePairTransportError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ReleasePairTransportError("release_pair_artifact_redirect_not_https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fail(reason: str) -> None:
    raise ReleasePairTransportError(reason)


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)
        if callable(junction) and junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _open_https(url: str, *, timeout: float, range_headers=None):
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail("release_pair_artifact_url_invalid")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "User-Agent": "GenshinCalc-release-pair-transport/1",
            **(range_headers or {}),
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    return opener.open(request, timeout=timeout)


def _download_https(
    locator: Mapping[str, Any],
    destination: Path,
    *,
    timeout: float,
    maximum_bytes: int,
    cancel_check: CancelCheck | None,
    cache_dir: Path | None = None,
    progress=None,
) -> dict[str, Any]:
    expected_size = locator["size_bytes"]
    if expected_size > maximum_bytes:
        _fail("release_pair_artifact_size_limit_exceeded")
    if cache_dir is not None:
        return _download_cached(locator, destination, cache_dir, timeout=timeout,
                                cancel_check=cancel_check, progress=progress)
    digest = hashlib.sha256()
    written = 0
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    try:
        with _open_https(locator["url"], timeout=timeout) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme.lower() != "https":
                _fail("release_pair_artifact_redirect_not_https")
            if getattr(response, "status", 200) != 200:
                _fail("release_pair_artifact_content_range_invalid")
            encoding = response.headers.get("Content-Encoding")
            if encoding not in (None, "", "identity"):
                _fail("release_pair_artifact_content_encoding_unsupported")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError:
                    _fail("release_pair_artifact_content_length_invalid")
                if declared_size != expected_size:
                    _fail("release_pair_artifact_size_mismatch")
            with temporary.open("xb") as output:
                while True:
                    if cancel_check is not None and cancel_check():
                        _fail("release_pair_artifact_download_cancelled")
                    block = response.read(CHUNK_BYTES)
                    if not block:
                        break
                    written += len(block)
                    if written > expected_size or written > maximum_bytes:
                        _fail("release_pair_artifact_size_mismatch")
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
        if written != expected_size:
            _fail("release_pair_artifact_size_mismatch")
        actual = digest.hexdigest()
        if actual != locator["sha256"]:
            _fail("release_pair_artifact_sha256_mismatch")
        os.replace(temporary, destination)
        return {"sha256": actual, "size_bytes": written}
    except ReleasePairTransportError:
        raise
    except (OSError, HTTPException) as error:
        raise ReleasePairTransportError(
            "release_pair_artifact_download_failed"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _cached_file_digest(path: Path, cancel_check: CancelCheck | None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_BYTES):
            if cancel_check is not None and cancel_check():
                _fail("release_pair_artifact_download_cancelled")
            digest.update(block)
    return digest.hexdigest()


def _download_cached(locator, destination, cache_dir, *, timeout, cancel_check, progress):
    from genshin_db.locking import LockBusyError, file_lock

    key, size = locator["sha256"], locator["size_bytes"]
    if os.path.lexists(destination):
        _fail("release_pair_artifact_destination_exists")
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        _fail("release_pair_artifact_cache_key_invalid")
    cache = Path(cache_dir).absolute()
    if any(_is_reparse_point(parent) for parent in (cache, *cache.parents)):
        _fail("release_pair_artifact_cache_path_invalid")
    cache.mkdir(parents=True, exist_ok=True)
    finished, partial, metadata, lock = (cache / f"{key}-{size}.{suffix}"
                                        for suffix in ("blob", "part", "json", "lock"))
    if any(_is_reparse_point(path) for path in (finished, partial, metadata, lock)):
        _fail("release_pair_artifact_cache_path_invalid")
    created_destination = False
    try:
        with file_lock(lock, timeout=0):
            if cancel_check is not None and cancel_check():
                _fail("release_pair_artifact_download_cancelled")
            if finished.is_file() and (finished.stat().st_size != size
                                       or _cached_file_digest(finished, cancel_check) != key):
                finished.unlink()
            cache_hit = finished.is_file()
            if not cache_hit:
                complete_partial = (partial.is_file() and partial.stat().st_size == size
                                    and _cached_file_digest(partial, cancel_check) == key)
                if not complete_partial:
                    _resume_download(locator, partial, metadata, timeout=timeout,
                                     cancel_check=cancel_check, progress=progress)
                    if partial.stat().st_size != size or _cached_file_digest(partial, cancel_check) != key:
                        partial.unlink(missing_ok=True)
                        metadata.unlink(missing_ok=True)
                        _fail("release_pair_artifact_sha256_mismatch")
                os.replace(partial, finished)
                metadata.unlink(missing_ok=True)
            with finished.open("rb") as source, destination.open("xb") as output:
                created_destination = True
                digest = hashlib.sha256()
                written = 0
                while block := source.read(CHUNK_BYTES):
                    if cancel_check is not None and cancel_check():
                        _fail("release_pair_artifact_download_cancelled")
                    written += len(block)
                    if written > size:
                        _fail("release_pair_artifact_size_mismatch")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
                if written != size or digest.hexdigest() != key:
                    _fail("release_pair_artifact_sha256_mismatch")
    except LockBusyError as error:
        raise ReleasePairTransportError("release_pair_artifact_download_busy") from error
    except (OSError, HTTPException) as error:
        if created_destination:
            destination.unlink(missing_ok=True)
        raise ReleasePairTransportError("release_pair_artifact_download_failed") from error
    except ReleasePairTransportError:
        if created_destination:
            destination.unlink(missing_ok=True)
        raise
    return {"sha256": key, "size_bytes": size, "download_reused": cache_hit}


def _resume_download(locator, partial, metadata, *, timeout, cancel_check, progress):
    url, size = locator["url"], locator["size_bytes"]
    offset, etag = 0, None
    try:
        if partial.is_file() and metadata.is_file() and metadata.stat().st_size <= 8192:
            previous = json.loads(metadata.read_text(encoding="utf-8"))
            if isinstance(previous, dict) and previous.get("url") == url and previous.get("size") == size:
                etag = previous.get("etag")
                if isinstance(etag, str) and re.fullmatch(r'"[^"\r\n]*"', etag):
                    offset = partial.stat().st_size
    except (OSError, ValueError):
        pass
    if offset >= size:
        offset = 0
    for start in (offset, 0) if offset else (0,):
        headers = {"Range": f"bytes={start}-", "If-Range": etag} if start else {}
        try:
            response = _open_https(url, timeout=timeout, range_headers=headers)
        except urllib.error.HTTPError as error:
            if start and error.code == 416:
                error.close()
                continue
            raise
        with response:
            if urllib.parse.urlparse(response.geturl()).scheme.lower() != "https":
                _fail("release_pair_artifact_redirect_not_https")
            if response.headers.get("Content-Encoding") not in (None, "", "identity"):
                _fail("release_pair_artifact_content_encoding_unsupported")
            tag = response.headers.get("ETag")
            status = getattr(response, "status", 200)
            if start and status == 206:
                if tag != etag or response.headers.get("Content-Range") != f"bytes {start}-{size - 1}/{size}":
                    continue
            elif status == 200:
                start = 0
            else:
                _fail("release_pair_artifact_content_range_invalid")
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdecimal() or int(declared) != size - start):
                _fail("release_pair_artifact_size_mismatch")
            with partial.open("ab" if start else "wb") as output:
                # Replace metadata only after opening/truncating the partial file.
                _write_json_atomic(metadata, {"url": url, "size": size, "etag": tag})
                began = time.monotonic()
                written = start
                while True:
                    if cancel_check is not None and cancel_check():
                        _fail("release_pair_artifact_download_cancelled")
                    try:
                        block = response.read(CHUNK_BYTES)
                    except IncompleteRead as error:
                        if len(error.partial) > size - written:
                            _fail("release_pair_artifact_size_mismatch")
                        output.write(error.partial)
                        raise
                    if not block:
                        break
                    written += len(block)
                    if written > size:
                        _fail("release_pair_artifact_size_mismatch")
                    output.write(block)
                    if progress is not None:
                        progress({"phase": "download", "bytes": written, "total_bytes": size,
                                  "bytes_per_second": (written - start) / max(time.monotonic() - began, 0.001),
                                  "resumed_bytes": start, "cancellable": True})
                output.flush()
                os.fsync(output.fileno())
            if written != size:
                _fail("release_pair_artifact_size_mismatch")
            return
    _fail("release_pair_artifact_content_range_invalid")


def _safe_zip_member(info: zipfile.ZipInfo, valid_parts: set[str] | None = None) -> PurePosixPath:
    name = info.orig_filename
    if not name or "\\" in name or "\x00" in name:
        _fail("release_pair_app_archive_member_invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or path.as_posix() != name.removesuffix("/"):
        _fail("release_pair_app_archive_member_invalid")
    for part in path.parts:
        if valid_parts is not None and part in valid_parts:
            continue
        if (part == ".." or part != part.rstrip(" .") or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or PureWindowsPath(part).is_reserved()):
            _fail("release_pair_app_archive_member_invalid")
        if valid_parts is not None and len(valid_parts) < 8192:
            valid_parts.add(part)
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        _fail("release_pair_app_archive_link_forbidden")
    return path


class _WindowsArchiveDirectories:
    """Anchor the active ZIP path with directory handles that deny rename."""

    def __init__(self, root: Path):
        from ctypes import wintypes

        class AttributeTag(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._open = kernel.CreateFileW
        self._open.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._open.restype = wintypes.HANDLE
        self._information = kernel.GetFileInformationByHandleEx
        self._information.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        self._information.restype = wintypes.BOOL
        self._close = kernel.CloseHandle
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL
        self._attribute_tag = AttributeTag
        self._root = root
        self._parts: tuple[str, ...] = ()
        self._handles = []

    def _pin(self, path: Path) -> None:
        # Read the directory's own attributes without following its final
        # reparse point. Denying delete sharing keeps the path stable while
        # writing a group of files through that directory.
        # FILE_LIST_DIRECTORY, FILE_SHARE_READ, OPEN_EXISTING, and
        # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.
        handle = self._open(str(path), 1, 1, None, 3, 0x02200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handles.append(handle)
        result = self._attribute_tag()
        if not self._information(handle, 9, ctypes.byref(result), ctypes.sizeof(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        if result.attributes & 0x400 or not result.attributes & 0x10:
            _fail("release_pair_app_archive_path_escape")

    def start(self) -> None:
        self._pin(self._root.parent)
        self._pin(self._root)

    def close(self) -> None:
        while self._handles:
            self._close(self._handles.pop())

    def ensure(self, relative: PurePosixPath) -> None:
        parts = relative.parts
        if parts == self._parts:
            return
        shared = 0
        for previous, current in zip(self._parts, parts):
            if previous != current:
                break
            shared += 1
        while len(self._handles) > shared + 2:
            self._close(self._handles.pop())
        current_dir = self._root.joinpath(*parts[:shared])
        for part in parts[shared:]:
            current_dir /= part
            current_dir.mkdir(exist_ok=True)
            self._pin(current_dir)
        self._parts = parts


@contextmanager
def _archive_directory_writer(root: Path, *, signed: bool):
    guard = _WindowsArchiveDirectories(root) if os.name == "nt" and signed else None

    def ensure(relative: PurePosixPath) -> Path:
        nonlocal guard
        directory = root.joinpath(*relative.parts)
        if guard is not None:
            try:
                guard.ensure(relative)
                return directory
            except OSError:
                # Some providers cannot pin directory handles. Keep the
                # current complete path validation for their remaining files.
                guard.close()
                guard = None
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve(strict=True)
        if resolved != directory:
            _fail("release_pair_app_archive_path_escape")
        return resolved

    try:
        if guard is not None:
            try:
                guard.start()
            except OSError:
                guard.close()
                guard = None
        yield ensure
    finally:
        if guard is not None:
            guard.close()


def _extract_app_archive(archive: Path, destination: Path, *, cancel_check: CancelCheck | None = None,
                         expected_manifest_sha256: str | None = None, workers: int | None = None) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if not members or len(members) > MAX_APP_MEMBERS:
                _fail("release_pair_app_archive_member_limit_exceeded")
            total = 0
            seen = set()
            valid_parts: set[str] = set()
            checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in members:
                member = _safe_zip_member(info, valid_parts)
                key = member.as_posix().casefold()
                if info.file_size < 0 or key in seen:
                    _fail("release_pair_app_archive_member_invalid")
                seen.add(key)
                total += info.file_size
                if total > MAX_APP_UNPACKED_BYTES:
                    _fail("release_pair_app_archive_size_limit_exceeded")
                checked.append((info, member))
            destination.mkdir(parents=True)
            root = destination.resolve(strict=True)
            if root != destination.absolute():
                _fail("release_pair_app_archive_path_escape")
            if expected_manifest_sha256 is not None:
                checked.sort(key=lambda item: item[1].parts)
            if workers is None:
                workers = _SIGNED_APP_EXTRACTION_WORKERS if len(checked) >= 2048 else 1
            with _archive_directory_writer(root, signed=expected_manifest_sha256 is not None) as ensure:
                verified_app = _write_app_archive(package, checked, root, ensure,
                    expected_manifest_sha256=expected_manifest_sha256, cancel_check=cancel_check, workers=workers)
            manifest = destination / PACKAGE_MANIFEST_FILENAME
            if not manifest.is_file() or _is_reparse_point(manifest):
                _fail("release_pair_app_package_manifest_missing")
            result = {
                "members": len(members),
                "files": sum(not info.is_dir() for info in members),
                "unpacked_size_bytes": total,
            }
            if verified_app is not None:
                result["verified_app"] = verified_app
            return result
    except ReleasePairTransportError:
        raise
    except PackageIntegrityError as error:
        raise ReleasePairTransportError(error.code) from error
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ReleasePairTransportError("release_pair_app_archive_invalid") from error


def _write_app_archive(package, checked, root, ensure, *, expected_manifest_sha256, cancel_check, workers):
    expectations = None
    if expected_manifest_sha256 is not None:
        manifest_info = next((info for info, member in checked
                              if member.as_posix() == PACKAGE_MANIFEST_FILENAME), None)
        if manifest_info is None or manifest_info.is_dir():
            _fail("release_pair_app_package_manifest_missing")
        if manifest_info.file_size > MAX_PACKAGE_MANIFEST_BYTES:
            _fail("supply_chain_package_manifest_too_large")
        if cancel_check is not None and cancel_check():
            _fail("release_pair_artifact_download_cancelled")
        manifest_bytes = package.read(manifest_info)
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
            _fail("release_pair_app_artifact_mismatch")
        with (root / PACKAGE_MANIFEST_FILENAME).open("xb") as output:
            output.write(manifest_bytes)
        _root, _raw, expectations = _read_package_manifest(root)

    if os.name == "nt" and expectations is not None and 1 < workers <= _MAX_APP_EXTRACTION_WORKERS:
        hashed_members, hashed_bytes = _write_verified_members_in_parallel(
            package, checked, root, ensure, expectations, cancel_check, workers,
        )
    else:
        hashed_members, hashed_bytes = _write_app_members(package, checked, ensure, expectations, cancel_check)
    if expectations is None:
        return None
    if hashed_members != expectations.keys():
        _fail("supply_chain_package_manifest_file_missing")
    directory = root.stat()
    return StagedAppVerification(directory.st_dev, directory.st_ino, expected_manifest_sha256,
                                 len(hashed_members), hashed_bytes)


def _write_app_members(package, checked, ensure, expectations, cancel_check):
    hashed_members = set()
    hashed_bytes = 0
    for info, member in checked:
        if cancel_check is not None and cancel_check():
            _fail("release_pair_artifact_download_cancelled")
        if info.is_dir():
            ensure(member)
            continue
        relative = member.as_posix()
        if expectations is not None and relative == PACKAGE_MANIFEST_FILENAME:
            continue
        member_digest = None
        if expectations is not None and relative not in PACKAGE_MANIFEST_EXCLUDED_PATHS:
            if relative not in expectations:
                _fail("supply_chain_package_manifest_file_unlisted")
            member_digest = hashlib.sha256()
        target = ensure(member.parent) / member.name
        with package.open(info, "r") as source, target.open("xb") as output:
            while block := source.read(CHUNK_BYTES):
                if cancel_check is not None and cancel_check():
                    _fail("release_pair_artifact_download_cancelled")
                output.write(block)
                if member_digest is not None:
                    member_digest.update(block)
        if member_digest is not None:
            if member_digest.hexdigest() != expectations[relative]:
                _fail("supply_chain_package_manifest_hash_mismatch")
            hashed_members.add(relative)
            hashed_bytes += info.file_size
    return hashed_members, hashed_bytes


def _write_verified_members_in_parallel(package, checked, root, ensure, expectations, cancel_check, workers):
    # File writes are disjoint. Validate explicit directories before jobs and
    # assign every file in the same case-insensitive parent to one job.
    files = {member.as_posix().casefold() for info, member in checked if not info.is_dir()}
    folders = {}
    for info, member in checked:
        directory = member if info.is_dir() else member.parent
        while directory.parts:
            name = directory.as_posix()
            folded = name.casefold()
            if folded in files or (folded in folders and folders[folded] != name):
                _fail("release_pair_app_archive_member_invalid")
            if folded in folders:
                break
            folders[folded] = name
            directory = directory.parent
        if info.is_dir():
            if cancel_check is not None and cancel_check():
                _fail("release_pair_artifact_download_cancelled")
            ensure(member)
    rows = sorted((row for row in checked if not row[0].is_dir()), key=lambda row: row[1].parent.parts)
    batches = [[] for _ in range(workers)]
    for _, group in groupby(rows, key=lambda row: row[1].parent):
        min(batches, key=len).extend(group)
    stopped = Event()
    cancel_lock = Lock()

    def cancelled():
        if stopped.is_set():
            return True
        if cancel_check is None:
            return False
        with cancel_lock:
            return cancel_check()

    def write(batch):
        with _archive_directory_writer(root, signed=True) as ensure_directory:
            return _write_app_members(package, batch, ensure_directory, expectations, cancelled)

    hashes = set()
    hashed_bytes = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(write, batch) for batch in batches if batch]
        try:
            finished, _pending = wait(jobs, return_when=FIRST_EXCEPTION)
            for job in finished:
                members, size = job.result()
                hashes.update(members)
                hashed_bytes += size
        finally:
            stopped.set()
    return hashes, hashed_bytes


def _validated_discovery(
    result: Mapping[str, Any], confirmed_document_sha256: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(result) is not dict or result.get("authoritative") is not True:
        _fail("release_pair_discovery_not_authoritative")
    document_digest = result.get("document_sha256")
    if (
        type(document_digest) is not str
        or len(document_digest) != 64
        or confirmed_document_sha256 != document_digest
    ):
        _fail("release_pair_update_confirmation_mismatch")
    artifacts_value = result.get("artifacts")
    if type(artifacts_value) is not dict or set(artifacts_value) != {"app", "database"}:
        _fail("release_pair_artifacts_invalid")
    artifacts = {
        "app": _validate_artifact(artifacts_value["app"], kind="app"),
        "database": _validate_artifact(artifacts_value["database"], kind="database"),
    }
    manifests = {
        "pair_manifest": copy.deepcopy(result.get("pair_manifest")),
        "database_manifest": copy.deepcopy(result.get("database_manifest")),
    }
    if any(type(value) is not dict for value in manifests.values()):
        _fail("release_pair_discovery_result_invalid")
    return artifacts, manifests


def discard_orphaned_release_staging(workspace: Path) -> bool:
    """Discard only transport-owned staging after the caller has excluded a live journal."""
    root = Path(workspace).absolute()
    staging = root / "staging"
    if not os.path.lexists(staging):
        return False
    if (any(_is_reparse_point(path) for path in (staging, root, *root.parents))
            or not staging.is_dir() or staging.resolve(strict=True).parent != root.resolve(strict=True)):
        _fail("release_pair_transport_workspace_invalid")

    def read_control(name):
        path = staging / name
        if not path.is_file() or _is_reparse_point(path):
            return None
        try:
            with path.open("rb") as source:
                raw = source.read(16 * 1024 + 1)
            return json.loads(raw) if len(raw) <= 16 * 1024 else None
        except (OSError, ValueError, RecursionError):
            return None

    if not any(staging.iterdir()):
        staging.rmdir()
        return True
    if read_control(_STAGING_OWNER_FILENAME) != _STAGING_OWNER:
        # Older releases leave a completed, self-consistent receipt instead.
        receipt = read_control(TRANSPORT_RECEIPT_FILENAME)
        if not isinstance(receipt, dict) or receipt.get("contract") != TRANSPORT_RECEIPT_CONTRACT:
            _fail("release_pair_transport_recovery_required")
        checksum = receipt.pop("receipt_sha256", None)
        if (receipt.get("schema_version") != TRANSPORT_SCHEMA_VERSION
                or hashlib.sha256(_canonical(receipt)).hexdigest() != checksum):
            _fail("release_pair_transport_recovery_required")
    shutil.rmtree(staging)
    return True


def stage_release_pair_artifacts(
    discovery_result: Mapping[str, Any],
    workspace: Path,
    *,
    confirmed_document_sha256: str,
    timeout: float = 30.0,
    cancel_check: CancelCheck | None = None,
    reuse_installed_database: bool = False,
    reuse_installed_app: bool = False,
    cache_dir: Path | None = None,
    progress=None,
    local_artifacts: Mapping[str, Path] | None = None,
    verify_app_on_extract: bool = False,
) -> dict[str, Any]:
    """Download one user-approved pair into ``workspace/staging``.

    Existing staging is never reused or deleted: recovery/cleanup belongs to
    the pair-update journal.  A failed fresh download removes only its private
    temporary directory. Callers that opt into streaming verification hold a
    private Setup workspace or the product update preparation lease. The durable
    transport receipt appears only after both artifacts are ready.
    """

    if type(timeout) not in (int, float) or not 0 < float(timeout) <= 300:
        _fail("release_pair_artifact_timeout_invalid")
    if (type(reuse_installed_database) is not bool or type(reuse_installed_app) is not bool
            or (reuse_installed_database and reuse_installed_app)):
        _fail("release_pair_artifact_mode_invalid")
    artifacts, manifests = _validated_discovery(
        discovery_result, confirmed_document_sha256
    )
    expected_local = {kind for kind, reused in (("app", reuse_installed_app),
                                                ("database", reuse_installed_database)) if not reused}
    if local_artifacts is not None and set(local_artifacts) != expected_local:
        _fail("release_pair_local_artifacts_invalid")
    root = Path(workspace).absolute()
    if any(_is_reparse_point(parent) for parent in (root, *root.parents)):
        _fail("release_pair_transport_workspace_invalid")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / "staging"
    if os.path.lexists(staging):
        _fail("release_pair_transport_recovery_required")
    temporary = staging if verify_app_on_extract else root / f".staging-{uuid4().hex}"
    try:
        temporary.mkdir()
    except FileExistsError as error:
        raise ReleasePairTransportError("release_pair_transport_recovery_required") from error
    try:
        _write_json_atomic(temporary / _STAGING_OWNER_FILENAME, _STAGING_OWNER)

        def stage(kind, destination, maximum_bytes):
            locator = artifacts[kind]
            if progress is not None:
                progress({"phase": "download" if local_artifacts is None else "verifying_archives",
                          "artifact": kind, "cancellable": True})
            if local_artifacts is None:
                return _download_https(locator, destination, timeout=float(timeout),
                    maximum_bytes=maximum_bytes, cancel_check=cancel_check,
                    **({"cache_dir": cache_dir, "progress": progress} if cache_dir is not None else {}))
            source = Path(local_artifacts[kind]).absolute()
            if any(_is_reparse_point(parent) for parent in (source, *source.parents)) or not source.is_file():
                _fail("release_pair_local_artifact_path_invalid")
            expected_size = locator["size_bytes"]
            if expected_size > maximum_bytes or source.stat().st_size != expected_size:
                _fail("release_pair_artifact_size_mismatch")
            digest, size = hashlib.sha256(), 0
            with source.open("rb") as incoming, destination.open("xb") as output:
                while block := incoming.read(CHUNK_BYTES):
                    if cancel_check is not None and cancel_check():
                        _fail("release_pair_artifact_download_cancelled")
                    size += len(block)
                    if size > expected_size:
                        _fail("release_pair_artifact_size_mismatch")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if size != expected_size or digest.hexdigest() != locator["sha256"]:
                _fail("release_pair_artifact_sha256_mismatch")
            return {"sha256": digest.hexdigest(), "size_bytes": size, "local_verified": True}

        app_archive = temporary / "app.zip"
        database = temporary / "genshin.db"
        app_download = {"download_skipped": True}
        extraction = None
        verified_app = None
        if not reuse_installed_app:
            app_download = stage("app", app_archive, MAX_APP_ARCHIVE_BYTES)
            if progress is not None:
                progress({"phase": "extract_app", "cancellable": True})
            extraction = _extract_app_archive(app_archive, temporary / "app", cancel_check=cancel_check,
                expected_manifest_sha256=(manifests["pair_manifest"]["app"]["artifact_manifest"]["sha256"]
                                          if verify_app_on_extract else None))
            verified_app = extraction.pop("verified_app", None)
            if (extraction["unpacked_size_bytes"] != artifacts["app"]["unpacked_size_bytes"]
                    or extraction["files"] != artifacts["app"]["unpacked_files"]):
                _fail("release_pair_app_archive_declared_shape_mismatch")
            app_archive.unlink()
        database_download = None
        if not reuse_installed_database:
            locator = artifacts["database"]
            compressed = locator["format"] == "sqlite-gzip"
            download_path = database.with_suffix(".db.gz") if compressed else database
            database_download = stage("database", download_path, MAX_DATABASE_BYTES)
            if compressed:
                if progress is not None:
                    progress({"phase": "extract_database", "cancellable": True})
                _extract_database(download_path, database, locator, cancel_check)
                download_path.unlink()
        receipt = {
            "contract": TRANSPORT_RECEIPT_CONTRACT,
            "schema_version": TRANSPORT_SCHEMA_VERSION,
            "document_sha256": confirmed_document_sha256,
            "release_pair_id": manifests["pair_manifest"].get("release_pair_id"),
            "database_release_id": manifests["database_manifest"].get(
                "database_release_id"
            ),
            "artifacts": {
                "app": {**copy.deepcopy(artifacts["app"]), **app_download},
                "database": (
                    {
                        **copy.deepcopy(artifacts["database"]),
                        **database_download,
                    }
                    if database_download is not None
                    else {
                        **copy.deepcopy(artifacts["database"]),
                        "download_skipped": True,
                    }
                ),
            },
            "database_mode": (
                "reuse_installed" if reuse_installed_database else "install"
            ),
            "staging": {
                "app_relative": None if reuse_installed_app else "app",
                "database_relative": (
                    None if reuse_installed_database else "genshin.db"
                ),
            },
            "app_extraction": extraction,
        }
        if reuse_installed_app:
            receipt["app_mode"] = "reuse_installed"
        receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()
        _write_json_atomic(temporary / TRANSPORT_RECEIPT_FILENAME, receipt)
        if temporary != staging:
            if os.path.lexists(staging):
                _fail("release_pair_transport_recovery_required")
            try:
                os.rename(temporary, staging)
            except OSError:
                # Some providers deny same-volume directory rename. Publish
                # the receipt only when all files have reached reserved staging.
                try:
                    staging.mkdir()
                except FileExistsError as error:
                    raise ReleasePairTransportError("release_pair_transport_recovery_required") from error
                _write_json_atomic(staging / _STAGING_OWNER_FILENAME, _STAGING_OWNER)
                shutil.copytree(temporary, staging, dirs_exist_ok=True, ignore=lambda path, _names:
                                (TRANSPORT_RECEIPT_FILENAME, _STAGING_OWNER_FILENAME) if Path(path) == temporary else ())
                _write_json_atomic(staging / TRANSPORT_RECEIPT_FILENAME, receipt)
                shutil.rmtree(temporary, ignore_errors=True)
        result = copy.deepcopy(receipt)
        if verify_app_on_extract:
            result["verified_app"] = verified_app
        return result
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _extract_database(archive: Path, destination: Path, locator: Mapping[str, Any],
                      cancel_check: CancelCheck | None) -> None:
    expected = locator["unpacked_size_bytes"]
    if expected > MAX_DATABASE_BYTES:
        _fail("release_pair_database_archive_size_exceeded")
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(archive, "rb") as source, destination.open("xb") as output:
            while chunk := source.read(CHUNK_BYTES):
                if cancel_check is not None and cancel_check():
                    _fail("release_pair_artifact_download_cancelled")
                size += len(chunk)
                if size > expected:
                    _fail("release_pair_database_archive_size_exceeded")
                digest.update(chunk)
                output.write(chunk)
    except (OSError, EOFError) as error:
        raise ReleasePairTransportError("release_pair_database_archive_invalid") from error
    if size != expected or digest.hexdigest() != locator["unpacked_sha256"]:
        _fail("release_pair_database_artifact_mismatch")


__all__ = [
    "TRANSPORT_RECEIPT_CONTRACT",
    "TRANSPORT_RECEIPT_FILENAME",
    "ReleasePairTransportError",
    "stage_release_pair_artifacts",
]
