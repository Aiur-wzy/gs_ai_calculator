"""Bounded transport for a signed App/database release-pair document.

The module only downloads and stages artifacts.  It never changes the active
App or database.  A caller must present the exact discovery-document digest
the user approved; every artifact is then streamed with size and SHA-256
checks into a fresh, isolated staging tree.
"""

from __future__ import annotations

import copy
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
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from genshin_app.release_pair_discovery import _validate_artifact

TRANSPORT_RECEIPT_CONTRACT = "genshin-release-pair-transport-receipt-v1"
TRANSPORT_RECEIPT_FILENAME = "release-pair-transport-receipt.json"
TRANSPORT_SCHEMA_VERSION = 1
MAX_APP_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_APP_UNPACKED_BYTES = 16 * 1024 * 1024 * 1024
MAX_APP_MEMBERS = 100_000
CHUNK_BYTES = 4 * 1024 * 1024

CancelCheck = Callable[[], bool]


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
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as error:
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
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as error:
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
                    block = response.read(CHUNK_BYTES)
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


def _safe_zip_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name or "\x00" in name:
        _fail("release_pair_app_archive_member_invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("release_pair_app_archive_member_invalid")
    if any(":" in part for part in path.parts):
        _fail("release_pair_app_archive_member_invalid")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        _fail("release_pair_app_archive_link_forbidden")
    return path


def _extract_app_archive(archive: Path, destination: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if not members or len(members) > MAX_APP_MEMBERS:
                _fail("release_pair_app_archive_member_limit_exceeded")
            total = 0
            checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in members:
                member = _safe_zip_member(info)
                if info.file_size < 0:
                    _fail("release_pair_app_archive_member_invalid")
                total += info.file_size
                if total > MAX_APP_UNPACKED_BYTES:
                    _fail("release_pair_app_archive_size_limit_exceeded")
                checked.append((info, member))
            destination.mkdir(parents=True)
            root = destination.resolve(strict=True)
            for info, member in checked:
                target = destination.joinpath(*member.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                resolved_parent = target.parent.resolve(strict=True)
                if root != resolved_parent and root not in resolved_parent.parents:
                    _fail("release_pair_app_archive_path_escape")
                with package.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, CHUNK_BYTES)
            manifest = destination / "PACKAGE-FILES.sha256"
            if not manifest.is_file() or _is_reparse_point(manifest):
                _fail("release_pair_app_package_manifest_missing")
            return {
                "members": len(members),
                "files": sum(not info.is_dir() for info in members),
                "unpacked_size_bytes": total,
            }
    except ReleasePairTransportError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ReleasePairTransportError("release_pair_app_archive_invalid") from error


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
) -> dict[str, Any]:
    """Download one user-approved pair into ``workspace/staging``.

    Existing staging is never reused or deleted: recovery/cleanup belongs to
    the pair-update journal.  A failed fresh download removes only its private
    temporary directory.
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
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    if _is_reparse_point(root):
        _fail("release_pair_transport_workspace_invalid")
    staging = root / "staging"
    if staging.exists():
        _fail("release_pair_transport_recovery_required")
    temporary = root / f".staging-{uuid4().hex}"
    temporary.mkdir()
    try:
        def stage(kind, destination, maximum_bytes):
            locator = artifacts[kind]
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
        if not reuse_installed_app:
            app_download = stage("app", app_archive, MAX_APP_ARCHIVE_BYTES)
            extraction = _extract_app_archive(app_archive, temporary / "app")
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
        try:
            os.replace(temporary, staging)
        except OSError:
            # Some Windows/AppContainer filesystems reject a directory rename
            # even on one volume.  ``staging`` is still fail-closed: the
            # receipt was written last, and any interrupted copy is treated as
            # recovery-required rather than a reusable candidate.
            if staging.exists():
                raise
            shutil.move(str(temporary), str(staging))
        return copy.deepcopy(receipt)
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
