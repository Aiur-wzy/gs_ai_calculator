"""Validate version-bound, offline licence review evidence.

This does not discover licences or grant permissions. Reviewed source documents
and conclusions are inputs, pinned to the exact package metadata (or runtime
binary). A changed dependency cannot silently inherit an earlier review.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

CONTRACT = "genshin-reviewed-license-evidence-v1"


class LicenseEvidenceError(ValueError):
    pass


def _plain(path: Path) -> None:
    if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise LicenseEvidenceError("license_evidence_reparse_point")


def _document(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    relative = value.get("path")
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise LicenseEvidenceError("license_evidence_path_invalid")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in ("..", ".") for p in relative.split("/")):
        raise LicenseEvidenceError("license_evidence_path_invalid")
    file = root
    for part in parts.parts:
        file = file / part
        _plain(file)
    if not file.is_file() or file.stat().st_size > 4 * 1024 * 1024:
        raise LicenseEvidenceError("license_evidence_document_invalid")
    content = file.read_bytes()
    if hashlib.sha256(content).hexdigest() != value.get("sha256"):
        raise LicenseEvidenceError("license_evidence_document_hash_mismatch")
    source = urlsplit(value.get("source_url", ""))
    if source.scheme != "https" or not source.netloc or source.username or source.password:
        raise LicenseEvidenceError("license_evidence_source_invalid")
    text = content.decode("utf-8")
    if not text.strip():
        raise LicenseEvidenceError("license_evidence_document_empty")
    return {**value, "text": text}


def load_license_evidence(path: Path) -> dict[str, dict[str, Any]]:
    """Read and verify documents; binding to installed identities is separate."""
    try:
        _plain(path)
        _plain(path.parent)
        if path.stat().st_size > 4 * 1024 * 1024:
            raise LicenseEvidenceError("license_evidence_manifest_invalid")
        payload = json.loads(path.read_bytes())
        if payload.get("contract") != CONTRACT or not isinstance(payload.get("components"), list):
            raise LicenseEvidenceError("license_evidence_manifest_invalid")
        result = {}
        for entry in payload["components"]:
            purl = entry.get("purl")
            if not isinstance(purl, str) or not purl.startswith("pkg:") or purl in result:
                raise LicenseEvidenceError("license_evidence_identity_invalid")
            binding = entry.get("binding", {})
            if set(binding) not in ({"package_metadata_sha256"}, {"binary_sha256"}):
                raise LicenseEvidenceError("license_evidence_binding_invalid")
            if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in binding.values()):
                raise LicenseEvidenceError("license_evidence_binding_invalid")
            if not all(isinstance(entry.get(k), str) and entry[k].strip()
                       for k in ("concluded_license", "resolution", "review_note")):
                raise LicenseEvidenceError("license_evidence_conclusion_missing")
            if not isinstance(entry.get("resolved_codes"), list) or not set(entry["resolved_codes"]) <= {
                "declared_license_missing", "license_text_missing",
            }:
                raise LicenseEvidenceError("license_evidence_review_scope_invalid")
            if not isinstance(entry.get("documents"), list) or not entry["documents"]:
                raise LicenseEvidenceError("license_evidence_documents_missing")
            documents = [_document(path.parent, v) for v in entry["documents"]]
            result[purl] = {**entry, "documents": documents}
        return result
    except LicenseEvidenceError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise LicenseEvidenceError("license_evidence_unreadable") from error


def bind_license_evidence(
    reviews: dict[str, dict[str, Any]], identities: dict[str, dict[str, str]],
) -> None:
    for purl, entry in reviews.items():
        if identities.get(purl) != entry["binding"]:
            raise LicenseEvidenceError(f"license_evidence_identity_mismatch:{purl}")


def public_review(entry: dict[str, Any]) -> dict[str, Any]:
    """No machine paths or duplicated document bodies in the review receipt."""
    return {
        key: value for key, value in entry.items() if key != "documents"
    } | {
        "documents": [
            {key: value for key, value in document.items() if key not in ("path", "text")}
            | {"notice_section": f"license-{document['sha256']}"}
            for document in entry["documents"]
        ],
    }
