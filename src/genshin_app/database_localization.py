"""显式离线补齐显示身份，先保存可恢复的数据库备份。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.display_names import migrate_display_names
from genshin_db.locking import database_lock
from genshin_db.validation import version_line as validate_line


def localize_database(db_path: Path, version_line: str = "release") -> dict:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "database_localization.localize_database")
    validate_line(version_line)
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    with database_lock(db_path):
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_dir / f"display-{stamp}-{version_line}.db"
        with (closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as source,
              closing(sqlite3.connect(backup)) as target):
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("中文显示升级前的备份校验失败，未修改数据库")
        backup_sha = hashlib.sha256(backup.read_bytes()).hexdigest()
        manifest = backup.with_suffix(".json")
        record = {"action": "localize_display_names", "db_path": str(db_path),
                  "version_line": version_line, "backup_path": str(backup),
                  "backup_sha256": backup_sha, "created_at": stamp, "state": "prepared"}
        manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            report = migrate_display_names(db_path, version_line)
        except Exception:
            record["state"] = "failed"
            manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        record.update(state="complete", report=report)
        manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**report, "backup_path": str(backup), "backup_sha256": backup_sha,
                "manifest_path": str(manifest)}
