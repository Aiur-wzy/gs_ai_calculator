"""候选构建诊断持久化；不写已接受的活动数据库。"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.locking import database_write

DDL = """
CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upstream_commit TEXT,
    version_line TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    validation TEXT,
    error TEXT
)
"""

_MAX_ERROR_LENGTH = 4096
_LOCAL_PATH_PATTERNS = (
    re.compile(r"'(?:[A-Za-z]:[\\/]|\\\\|/)[^'\r\n]*'"),
    re.compile(r'"(?:[A-Za-z]:[\\/]|\\\\|/)[^"\r\n]*"'),
    re.compile(r"file:///(?:[A-Za-z]:/|/)[^\s'\"<>|]+", re.IGNORECASE),
    re.compile(r"(?<![\w:/])(?:[A-Za-z]:[\\/]|\\\\)[^\s'\"<>|]+"),
)


def sanitize_import_error(error: str | None) -> str | None:
    """Keep failure diagnostics useful without persisting machine-local paths."""

    if error is None:
        return None
    value = str(error)
    for pattern in _LOCAL_PATH_PATTERNS:
        value = pattern.sub("<local-path>", value)
    if len(value) > _MAX_ERROR_LENGTH:
        value = value[: _MAX_ERROR_LENGTH - len("...[truncated]")] + "...[truncated]"
    return value


@database_write
def record_import(db_path: Path, commit: str | None, started_at: str, status: str,
                  counts: dict | None = None, validation: str | None = None,
                  error: str | None = None, version_line: str = "release") -> None:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "import_log.record_import")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(DDL)
            conn.execute(
                "INSERT INTO import_log(upstream_commit,version_line,started_at,finished_at,"
                "status,counts_json,validation,error) VALUES(?,?,?,?,?,?,?,?)",
                (commit, version_line, started_at, datetime.now(UTC).isoformat(), status,
                 json.dumps(counts or {}, ensure_ascii=False), validation,
                 sanitize_import_error(error)),
            )
    finally:
        conn.close()
