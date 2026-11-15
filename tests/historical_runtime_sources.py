"""Exact historical test bytes; never consult the current upstream checkout.

The ZIP contains original Git blobs at one reviewed commit. Only the declared
LF-to-CRLF workspace representation is materialized, and both identities are
checked. This data fixture carries no App, DB or engine execution authority.
"""
from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

HISTORICAL_REVIEWED_COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"
_ROOT = Path(__file__).resolve().parent / "fixtures/historical-runtime-sources-224708ed"
_MANIFEST_SHA256 = "111b78bd7b24c45af4541554cf7110ba2e2161cc57943cab957d4c238689ee5e"
_CONTRACT = "genshin-historical-runtime-source-snapshot-v1"


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("historical_source_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("historical_source_path_invalid")
    return value


def _read_verified_member(data, manifest, source_path):
    """Byte-only verifier; explicit inner seam for validly resealed negatives."""
    if manifest["contract"] != _CONTRACT or manifest["commit"] != HISTORICAL_REVIEWED_COMMIT:
        raise ValueError("historical_source_snapshot_identity")
    if len(data) != manifest["archive"]["size_bytes"] or _sha(data) != manifest["archive"]["sha256"]:
        raise ValueError("historical_source_archive_identity")
    rows = manifest["sources"]
    expected = {_safe_path(row["source_path"]): row for row in rows}
    if (len(expected) != len(rows) or len(expected) != manifest["member_count"]
            or len({name.casefold() for name in expected}) != len(expected)):
        raise ValueError("historical_source_manifest_members")
    if source_path not in expected:
        raise KeyError("historical_source_not_in_snapshot:" + source_path)
    row = expected[source_path]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("historical_source_archive_members")
        for name in names:
            info = archive.getinfo(name)
            if (info.is_dir() or stat.S_IFMT(info.external_attr >> 16) != stat.S_IFREG
                    or info.file_size != expected[name]["git_blob_bytes"]):
                raise ValueError("historical_source_member_type_size")
        blob = archive.read(source_path)
    if _sha(blob) != row["git_blob_sha256"] or len(blob) != row["git_blob_bytes"]:
        raise ValueError("historical_source_blob_identity")
    if row["materialization"] != "lf_to_historical_crlf" or b"\r" in blob:
        raise ValueError("historical_source_representation")
    workspace = blob.replace(b"\n", b"\r\n")
    if _sha(workspace) != row["workspace_sha256"] or len(workspace) != row["workspace_bytes"]:
        raise ValueError("historical_source_workspace_identity")
    return workspace


def read_source_bytes(source_path: str) -> bytes:
    """Read one fixed reviewed file; missing/corrupt fixtures fail, never skip."""
    source_path = _safe_path(source_path)
    manifest_bytes = (_ROOT / "manifest.json").read_bytes()
    if _sha(manifest_bytes) != _MANIFEST_SHA256:
        raise ValueError("historical_source_manifest_identity")
    manifest = json.loads(manifest_bytes)
    if manifest["archive"]["filename"] != "runtime-sources-224708ed.zip":
        raise ValueError("historical_source_archive_name")
    data = (_ROOT / manifest["archive"]["filename"]).read_bytes()
    return _read_verified_member(data, manifest, source_path)
