from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import zipfile
from http.client import IncompleteRead
from pathlib import Path, PurePosixPath

import pytest

from genshin_app import release_pair_transport as transport


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _app_archive(path: Path, *, unsafe: str | None = None) -> bytes:
    app = b"application"
    manifest = f"{_sha(app)}  app.bin\n".encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("PACKAGE-FILES.sha256", manifest)
        package.writestr("app.bin", app)
        if unsafe is not None:
            package.writestr(unsafe, b"escape")
    return path.read_bytes()


def _result(app: bytes, database: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(app)) as package:
        infos = package.infolist()
        unpacked_size = sum(info.file_size for info in infos)
        unpacked_files = sum(not info.is_dir() for info in infos)
    return {
        "authoritative": True,
        "document_sha256": "d" * 64,
        "pair_manifest": {"release_pair_id": "pair-next"},
        "database_manifest": {"database_release_id": "db-next"},
        "artifacts": {
            "app": {
                "url": "https://updates.example/app.zip",
                "sha256": _sha(app),
                "size_bytes": len(app),
                "format": "zip",
                "unpacked_size_bytes": unpacked_size,
                "unpacked_files": unpacked_files,
            },
            "database": {
                "url": "https://updates.example/genshin.db",
                "sha256": _sha(database),
                "size_bytes": len(database),
                "format": "sqlite",
            },
        },
    }


def test_stage_binds_confirmation_and_publishes_only_complete_tree(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    database = b"sqlite-package"
    result = _result(app, database)

    def download(locator, destination, **_kwargs):
        payload = app if locator["format"] == "zip" else database
        destination.write_bytes(payload)
        return {"sha256": _sha(payload), "size_bytes": len(payload)}

    monkeypatch.setattr(transport, "_download_https", download)
    workspace = tmp_path / "workspace"
    receipt = transport.stage_release_pair_artifacts(
        result,
        workspace,
        confirmed_document_sha256="d" * 64,
    )
    assert receipt["release_pair_id"] == "pair-next"
    assert (workspace / "staging" / "app" / "app.bin").read_bytes() == b"application"
    assert (workspace / "staging" / "genshin.db").read_bytes() == database
    assert (workspace / "staging" / transport.TRANSPORT_RECEIPT_FILENAME).is_file()
    assert not list(workspace.glob(".staging-*"))

    with pytest.raises(transport.ReleasePairTransportError) as stale:
        transport.stage_release_pair_artifacts(
            result,
            tmp_path / "other",
            confirmed_document_sha256="0" * 64,
        )
    assert stale.value.reason == "release_pair_update_confirmation_mismatch"


def test_verified_staging_uses_signed_member_digests_and_keeps_proof_in_process(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    database = tmp_path / "source.db"
    database.write_bytes(b"sqlite-package")
    result = _result(app, database.read_bytes())
    with zipfile.ZipFile(app_path) as package:
        manifest_sha256 = _sha(package.read("PACKAGE-FILES.sha256"))
    result["pair_manifest"]["app"] = {"artifact_manifest": {"sha256": manifest_sha256}}

    workspace = tmp_path / "work"
    staging = workspace / "staging"
    seen_extraction = []

    def progress(event):
        if event["phase"] == "extract_app":
            seen_extraction.append(True)
            assert staging.is_dir() and not (staging / transport.TRANSPORT_RECEIPT_FILENAME).exists()

    monkeypatch.setattr(transport.os, "rename", lambda *_: pytest.fail("private staging should not need a rename"))
    receipt = transport.stage_release_pair_artifacts(result, workspace, confirmed_document_sha256="d" * 64,
        local_artifacts={"app": app_path, "database": database}, verify_app_on_extract=True, progress=progress)
    disk_receipt = json.loads((staging / transport.TRANSPORT_RECEIPT_FILENAME).read_bytes())
    proof = receipt.pop("verified_app")
    assert receipt == disk_receipt and seen_extraction
    source = staging / "app"
    assert proof.report_for(source)["files"] == 1
    copied = tmp_path / "copy"
    shutil.copytree(source, copied)
    assert proof.report_for(copied) is None


@pytest.mark.parametrize(("manifest", "payload", "extra", "error"), [
    ("bad-manifest", b"application", None, "release_pair_app_artifact_mismatch"),
    ("signed", b"APPLICATION", None, "supply_chain_package_manifest_hash_mismatch"),
    ("signed", b"application", "unlisted.dll", "supply_chain_package_manifest_file_unlisted"),
])
def test_verified_extraction_rejects_wrong_manifest_and_members(tmp_path, manifest, payload, extra, error):
    archive = tmp_path / "app.zip"
    raw = f"{_sha(b'application')}  app.bin\n".encode()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("PACKAGE-FILES.sha256", raw if manifest == "signed" else b"different")
        package.writestr("app.bin", payload)
        if extra is not None:
            package.writestr(extra, b"unsigned")
    with pytest.raises(transport.ReleasePairTransportError, match=error):
        transport._extract_app_archive(archive, tmp_path / "app", expected_manifest_sha256=_sha(raw))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
def test_verified_archive_writer_denies_rename_of_active_prefix(tmp_path):
    root = tmp_path.resolve() / "app"
    root.mkdir()
    with transport._archive_directory_writer(root, signed=True) as ensure:
        directory = ensure(PurePosixPath("runtime/nested"))
        (directory / "member.js").write_bytes(b"asset")
        with pytest.raises(OSError):
            directory.parent.rename(root / "moved")
        assert ensure(PurePosixPath("runtime/nested")) == directory
    directory.parent.rename(root / "moved")
    assert (root / "moved/nested/member.js").read_bytes() == b"asset"


@pytest.mark.skipif(os.name != "nt", reason="parallel extraction is Windows-only")
def test_parallel_extraction_verifies_the_same_files_and_digests(tmp_path):
    archive = tmp_path / "app.zip"
    files = {f"internal/runtime-{index // 4}/asset-{index}.js": b"asset" * (index + 1) for index in range(24)}
    raw = "".join(f"{_sha(body)}  {name}\n" for name, body in sorted(files.items())).encode()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("PACKAGE-FILES.sha256", raw)
        for name, body in reversed(list(files.items())):
            package.writestr(name, body)
    baseline, parallel = tmp_path / "serial", tmp_path / "parallel"
    reports = []
    for workers, destination in ((1, baseline), (4, parallel)):
        result = transport._extract_app_archive(archive, destination, expected_manifest_sha256=_sha(raw), workers=workers)
        reports.append(result["verified_app"].report_for(destination))
        assert {path.relative_to(destination).as_posix(): path.read_bytes() for path in destination.rglob("*.js")} == files
    assert reports[0] == reports[1] and reports[1]["files"] == len(files)


@pytest.mark.skipif(os.name != "nt", reason="parallel extraction is Windows-only")
@pytest.mark.parametrize(("fault", "error"), [
    ("corruption", "manifest_hash_mismatch"),
    ("cancel", "download_cancelled"),
    ("alias", "archive_member_invalid"),
])
def test_parallel_extraction_aborts_on_invalid_bytes_cancel_or_path_alias(tmp_path, fault, error):
    archive = tmp_path / "app.zip"
    names = ["runtime/alpha/module.js", "runtime/beta/module.js", "runtime/gamma/module.js"]
    if fault == "alias":
        names[1] = "Runtime/beta/module.js"
    raw = "".join(f"{_sha(b'abc')}  {name}\n" for name in sorted(names)).encode()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("PACKAGE-FILES.sha256", raw)
        for index, name in enumerate(names):
            package.writestr(name, b"ABC" if index == 1 and fault == "corruption" else b"abc")
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        return fault == "cancel" and checks >= 4

    with pytest.raises(transport.ReleasePairTransportError, match=error):
        transport._extract_app_archive(archive, tmp_path / "app", expected_manifest_sha256=_sha(raw), workers=4,
                                       cancel_check=cancel)


def test_stage_copy_fallback_keeps_incomplete_tree_without_receipt(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    database = tmp_path / "source.db"
    database.write_bytes(b"sqlite-package")
    result = _result(app, database.read_bytes())
    workspace = tmp_path / "update"
    staging = workspace / "staging"

    def denied_rename(*_args):
        raise PermissionError("fixture cannot rename staged directory")

    def interrupted_copy(source, destination, *, dirs_exist_ok, ignore):
        assert Path(destination) == staging and staging.is_dir() and dirs_exist_ok
        assert transport.TRANSPORT_RECEIPT_FILENAME in ignore(str(source), [])
        (staging / "part").write_bytes(b"copy not complete")
        raise OSError("fixture was interrupted during copy")

    with monkeypatch.context() as fault:
        fault.setattr(transport.os, "rename", denied_rename)
        fault.setattr(transport.shutil, "copytree", interrupted_copy)
        with pytest.raises(OSError, match="interrupted during copy"):
            transport.stage_release_pair_artifacts(result, workspace, confirmed_document_sha256="d" * 64,
                                                   local_artifacts={"app": app_path, "database": database})
    assert (staging / "part").is_file()
    assert not (staging / transport.TRANSPORT_RECEIPT_FILENAME).exists()
    assert not list(workspace.glob(".staging-*"))
    with pytest.raises(transport.ReleasePairTransportError, match="recovery_required"):
        transport.stage_release_pair_artifacts(result, workspace, confirmed_document_sha256="d" * 64)
    assert transport.discard_orphaned_release_staging(workspace)
    assert not staging.exists()
    transport.stage_release_pair_artifacts(result, workspace, confirmed_document_sha256="d" * 64,
                                           local_artifacts={"app": app_path, "database": database})
    assert (staging / transport.TRANSPORT_RECEIPT_FILENAME).is_file()


def test_unknown_staging_tree_is_not_removed(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    sentinel = staging / "unknown.json"
    sentinel.write_text('{"keep":true}', encoding="utf-8")
    with pytest.raises(transport.ReleasePairTransportError, match="recovery_required"):
        transport.discard_orphaned_release_staging(tmp_path)
    assert sentinel.is_file()


def test_stage_app_only_reuse_never_downloads_database(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    result = _result(app, b"database-that-must-not-download")
    calls = []

    def download(locator, destination, **_kwargs):
        calls.append(locator["format"])
        assert locator["format"] == "zip"
        destination.write_bytes(app)
        return {"sha256": _sha(app), "size_bytes": len(app)}

    monkeypatch.setattr(transport, "_download_https", download)
    workspace = tmp_path / "workspace"
    receipt = transport.stage_release_pair_artifacts(
        result,
        workspace,
        confirmed_document_sha256="d" * 64,
        reuse_installed_database=True,
    )

    assert calls == ["zip"]
    assert receipt["database_mode"] == "reuse_installed"
    assert receipt["artifacts"]["database"]["download_skipped"] is True
    assert receipt["staging"]["database_relative"] is None
    assert not (workspace / "staging" / "genshin.db").exists()


def test_stage_rejects_signed_unpacked_shape_mismatch(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    database = b"sqlite-package"
    result = _result(app, database)
    result["artifacts"]["app"]["unpacked_files"] += 1

    def download(locator, destination, **_kwargs):
        payload = app if locator["format"] == "zip" else database
        destination.write_bytes(payload)
        return {"sha256": _sha(payload), "size_bytes": len(payload)}

    monkeypatch.setattr(transport, "_download_https", download)
    workspace = tmp_path / "workspace"
    with pytest.raises(transport.ReleasePairTransportError) as failure:
        transport.stage_release_pair_artifacts(
            result,
            workspace,
            confirmed_document_sha256="d" * 64,
        )
    assert failure.value.reason == "release_pair_app_archive_declared_shape_mismatch"
    assert not (workspace / "staging").exists()


def test_failed_stage_removes_only_private_temporary_directory(tmp_path, monkeypatch):
    app_path = tmp_path / "source.zip"
    app = _app_archive(app_path)
    result = _result(app, b"database")
    calls = 0

    def fail_second(locator, destination, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise transport.ReleasePairTransportError("network")
        destination.write_bytes(app)
        return {"sha256": _sha(app), "size_bytes": len(app)}

    monkeypatch.setattr(transport, "_download_https", fail_second)
    workspace = tmp_path / "workspace"
    keep = workspace / "keep.txt"
    workspace.mkdir()
    keep.write_text("user", encoding="utf-8")
    with pytest.raises(transport.ReleasePairTransportError):
        transport.stage_release_pair_artifacts(
            result,
            workspace,
            confirmed_document_sha256="d" * 64,
        )
    assert keep.read_text(encoding="utf-8") == "user"
    assert not (workspace / "staging").exists()
    assert not list(workspace.glob(".staging-*"))


def test_zip_path_traversal_and_links_are_rejected(tmp_path):
    archive = tmp_path / "unsafe.zip"
    _app_archive(archive, unsafe="../outside.txt")
    with pytest.raises(transport.ReleasePairTransportError) as failure:
        transport._extract_app_archive(archive, tmp_path / "app")
    assert failure.value.reason == "release_pair_app_archive_member_invalid"
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("member", ["docs//example.txt", "docs/./example.txt", "docs/trailing.", "APP.BIN"])
def test_zip_rejects_nonportable_names_before_extraction(tmp_path, member):
    archive = tmp_path / "unsafe.zip"
    _app_archive(archive, unsafe=member)
    target = tmp_path / "app"
    with pytest.raises(transport.ReleasePairTransportError, match="archive_member_invalid"):
        transport._extract_app_archive(archive, target)
    assert not target.exists()


def test_stage_app_only_can_cancel_during_extraction_without_a_ready_receipt(tmp_path, monkeypatch):
    app = _app_archive(tmp_path / "source.zip")
    workspace = tmp_path / "work"
    result = _result(app, b"reused database")
    active = False

    def downloaded(_locator, destination, **_kwargs):
        nonlocal active
        destination.write_bytes(app)
        active = True
        return {"sha256": _sha(app), "size_bytes": len(app)}

    monkeypatch.setattr(transport, "_download_https", downloaded)
    with pytest.raises(transport.ReleasePairTransportError, match="download_cancelled"):
        transport.stage_release_pair_artifacts(result, workspace, confirmed_document_sha256="d" * 64,
                                               reuse_installed_database=True, cancel_check=lambda: active)
    assert not (workspace / "staging").exists() and not list(workspace.glob(".staging-*"))


class _Response:
    def __init__(self, body: bytes):
        self._stream = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return "https://cdn.example/artifact"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_stream_download_checks_size_hash_and_cancellation(tmp_path, monkeypatch):
    body = b"bounded payload"
    locator = {
        "url": "https://updates.example/file",
        "sha256": _sha(body),
        "size_bytes": len(body),
        "format": "sqlite",
    }
    monkeypatch.setattr(
        transport, "_open_https", lambda *_args, **_kwargs: _Response(body)
    )
    destination = tmp_path / "file"
    assert transport._download_https(
        locator,
        destination,
        timeout=1,
        maximum_bytes=1024,
        cancel_check=None,
    )["sha256"] == _sha(body)
    assert destination.read_bytes() == body

    with pytest.raises(transport.ReleasePairTransportError) as cancelled:
        transport._download_https(
            locator,
            tmp_path / "cancelled",
            timeout=1,
            maximum_bytes=1024,
            cancel_check=lambda: True,
        )
    assert cancelled.value.reason == "release_pair_artifact_download_cancelled"
    assert not (tmp_path / "cancelled").exists()


@pytest.mark.parametrize("unchanged", [True, False])
def test_cancelled_download_resumes_only_a_verified_range(tmp_path, monkeypatch, unchanged):
    body = b"one-two-three"
    locator = {"url": "https://updates.example/file", "sha256": _sha(body),
               "size_bytes": len(body), "format": "sqlite"}
    calls, progress = [], []
    monkeypatch.setattr(transport, "CHUNK_BYTES", 4)

    def fetch(_url, *, timeout, range_headers):
        calls.append(range_headers)
        start = 4 if range_headers and unchanged else 0
        response = _Response(body[start:])
        response.status = 206 if start else 200
        response.headers["ETag"] = '"v1"' if not range_headers or unchanged else '"v2"'
        if start:
            response.headers["Content-Range"] = f"bytes 4-{len(body) - 1}/{len(body)}"
        return response

    monkeypatch.setattr(transport, "_open_https", fetch)
    settings = {"timeout": 1, "maximum_bytes": 1024, "cache_dir": tmp_path / "cache"}
    with pytest.raises(transport.ReleasePairTransportError, match="download_cancelled"):
        transport._download_https(locator, tmp_path / "interrupted", **settings,
                                  cancel_check=lambda: bool(progress), progress=progress.append)
    assert not (tmp_path / "interrupted").exists()
    result = transport._download_https(locator, tmp_path / "complete", **settings, cancel_check=None)
    assert (tmp_path / "complete").read_bytes() == body and result["download_reused"] is False
    assert calls == [{}, {"Range": "bytes=4-", "If-Range": '"v1"'}]
    assert transport._download_https(locator, tmp_path / "reused", **settings,
                                     cancel_check=None)["download_reused"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("partial_size", [5, 13])
def test_incomplete_http_read_preserves_verified_resume_path(tmp_path, monkeypatch, partial_size):
    raw = b"one-two-three"
    locator = {"url": "https://updates.example/file", "sha256": _sha(raw), "size_bytes": len(raw)}
    cache = tmp_path / "cache"
    calls = []

    class InterruptedResponse(_Response):
        def read(self, size=-1):
            raise IncompleteRead(raw[:partial_size], len(raw) - partial_size)

    def fetch(_url, *, timeout, range_headers):
        calls.append(range_headers)
        if len(calls) == 1:
            response = InterruptedResponse(raw)
        else:
            assert range_headers == {"Range": f"bytes={partial_size}-", "If-Range": '"v1"'}
            response = _Response(raw[partial_size:])
            response.status = 206
            response.headers["Content-Range"] = f"bytes {partial_size}-{len(raw) - 1}/{len(raw)}"
        response.headers["ETag"] = '"v1"'
        return response

    monkeypatch.setattr(transport, "_open_https", fetch)
    destination = tmp_path / "file"
    options = {"timeout": 1, "maximum_bytes": 1024, "cache_dir": cache, "cancel_check": None}
    with pytest.raises(transport.ReleasePairTransportError, match="download_failed"):
        transport._download_https(locator, destination, **options)
    assert not destination.exists()
    assert next(cache.glob("*.part")).read_bytes() == raw[:partial_size]
    transport._download_https(locator, destination, **options)
    assert destination.read_bytes() == raw
    assert len(calls) == (1 if partial_size == len(raw) else 2)
