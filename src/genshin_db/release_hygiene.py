"""Verify actual SQLite release bytes and compressed source payloads for local data."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from genshin_db.import_log import sanitize_import_error
from genshin_db.runtime_store import decode_source_bytes

_BLOCK_BYTES = 4 * 1024 * 1024
_SOURCE_TABLES = ("meta", "annotation", "character_note", "entity_publication", "import_log", "raw_table")
_PRIVATE = re.compile(
    rb"(?:[a-z]:[/\\]+users[/\\]+|file:///|/(?:home|users)/[^/\s\x00]{1,96}/|"
    rb"-----begin (?:rsa |ec |openssh |encrypted )?private key-----|"
    rb"\bgh[pousr]_[a-z0-9]{30,}\b|\bgithub_pat_[a-z0-9_]{40,}\b|"
    rb"\bsk-(?:proj-)?[a-z0-9_\-]{30,}\b|\b(?:akia|asia)[a-z0-9]{16}\b)",
    re.IGNORECASE,
)


def _workspace_needles(workspace_root):
    root = Path(workspace_root).resolve()
    native = str(root)
    variants = {native, root.as_posix(), json.dumps(native, ensure_ascii=True)[1:-1]}
    return tuple(encoded.lower() for variant in variants
                 for encoded in (variant.encode("utf-8"), variant.encode("utf-16le")))


def _check_payload(value, needles):
    lower = value.lower()
    if any(needle in lower for needle in needles):
        raise ValueError("database_release_contains_workspace_path_bytes")
    if _PRIVATE.search(value):
        raise ValueError("database_release_contains_private_path_or_credential")


def validate_database_release_hygiene(database: Path, *, workspace_root: Path) -> dict:
    """Inspect live data and raw pages, then verify and scan every compressed source."""
    database = Path(database).resolve()
    needles = _workspace_needles(workspace_root)
    compressed = logical_rows = 0
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("database_release_integrity_failed")
            if connection.execute("PRAGMA freelist_count").fetchone()[0] != 0:
                raise ValueError("database_release_freelist_not_empty")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if "import_log" in tables:
                errors = connection.execute("SELECT error FROM import_log WHERE error IS NOT NULL")
                if any(sanitize_import_error(error) != error for (error,) in errors):
                    raise ValueError("database_release_import_log_contains_local_path")
            for table in _SOURCE_TABLES:
                if table not in tables:
                    continue
                # Inspect logical values as well as raw pages. Long text can be
                # split by SQLite overflow pages, hiding a signature from a
                # scan of the physical byte stream alone.
                for row in connection.execute(f'SELECT * FROM "{table}"'):
                    for value in row:
                        if isinstance(value, str):
                            _check_payload(value.encode("utf-8"), needles)
                    logical_rows += 1
            if "runtime_source" in tables:
                rows = connection.execute("SELECT source_size_bytes, payload_codec, payload_blob, source_sha256 FROM runtime_source")
                for row in rows:
                    _check_payload(decode_source_bytes(row), needles)
                    compressed += 1
    except (OSError, sqlite3.Error, TypeError) as error:
        raise ValueError("database_release_sqlite_invalid") from error
    overlap = max(1024, max(map(len, needles), default=1))
    carry = b""
    with database.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_BLOCK_BYTES), b""):
            window = carry + chunk
            _check_payload(window, needles)
            carry = window[-overlap:]
    return {"integrity_check": "ok", "freelist_count": 0,
            "compressed_sources_scanned": compressed, "logical_rows_scanned": logical_rows,
            "sqlite_bytes_scanned": database.stat().st_size}
