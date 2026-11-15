"""Stable, length-preserving projection for model-visible native text.

The native Agent paginates trusted bundled documentation by character offset.
Redaction therefore must happen before pagination and must not change the text
length, otherwise a local path or credential spanning two pages can either
leak or move the continuation cursor.
"""

from __future__ import annotations

import re

_WINDOWS_DRIVE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
_WINDOWS_UNC_PATH = re.compile(r"(?i)(?<![\\])\\\\+(?=[^\\\s])")
_FORWARD_UNC_PATH = re.compile(r"(?<![:/])//(?=[^/\s])")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9:/])/(?:home|users|root|tmp|var|etc|opt|usr|mnt|volumes|private)(?:/|\b)"
)
_FILE_URI_PATH = re.compile(r"(?i)\bfile:(?://)?")
_PUBLIC_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_SECRET = re.compile(
    r"(?ix)(?:\b(?:api[\s_-]*key|authorization|access[\s_-]*token|token|"
    r"secret|password|passwd|pwd)\b\s*[:=]\s*(?:bearer\s+)?"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;，。；、]+)"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{8,})"
)
_PATH_SUFFIX_BOUNDARY = re.compile(
    r"(?:\r\n|[\r\n\t])|(?:\s+(?:and|then|or|but|from|at|以及|然后|并且|随后)\s+)"
    r"|(?:[;,](?=\s|$))|(?:\.(?=\s|$))|[\)\]\}>，。；、！？]"
)


def _masked(length: int, marker: str) -> str:
    if length >= len(marker):
        return marker + "•" * (length - len(marker))
    return "•" * length


def sanitize_public_text(value: str) -> str:
    """Redact precise local paths and credentials without moving offsets."""

    if not isinstance(value, str):
        raise TypeError("model-visible text must be a string")
    public_ranges = tuple(
        (match.start(), match.end()) for match in _PUBLIC_HTTP_URL.finditer(value)
    )

    def public_url_contains(index: int) -> bool:
        return any(start <= index < end for start, end in public_ranges)

    def path_end(start: int, marker_end: int) -> int:
        if start > 0 and value[start - 1] in {'"', "'"}:
            closing = value.find(value[start - 1], marker_end)
            if closing >= 0:
                return closing
        boundary = _PATH_SUFFIX_BOUNDARY.search(value, marker_end)
        return len(value) if boundary is None else boundary.start()

    candidates: list[tuple[int, int, str, int]] = [
        (match.start(), match.end(), "[redacted-secret]", 0)
        for match in _SECRET.finditer(value)
    ]
    for pattern, replacement in (
        (_FILE_URI_PATH, "[redacted-uri]"),
        (_WINDOWS_UNC_PATH, "[redacted-path]"),
        (_FORWARD_UNC_PATH, "[redacted-path]"),
        (_WINDOWS_DRIVE_PATH, "[redacted-path]"),
        (_POSIX_ABSOLUTE_PATH, "[redacted-path]"),
    ):
        for match in pattern.finditer(value):
            if not public_url_contains(match.start()):
                candidates.append(
                    (match.start(), path_end(match.start(), match.end()), replacement, 1)
                )
    if not candidates:
        return value

    selected: list[tuple[int, int, str]] = []
    cursor = -1
    for start, end, replacement, priority in sorted(
        candidates, key=lambda row: (row[0], row[3], -row[1])
    ):
        if start < cursor:
            continue
        selected.append((start, end, replacement))
        cursor = end

    output: list[str] = []
    cursor = 0
    for start, end, replacement in selected:
        output.append(value[cursor:start])
        output.append(_masked(end - start, replacement))
        cursor = end
    output.append(value[cursor:])
    projected = "".join(output)
    if len(projected) != len(value):  # Defensive invariant for pagination.
        raise RuntimeError("native text projection changed character length")
    return projected


__all__ = ["sanitize_public_text"]
