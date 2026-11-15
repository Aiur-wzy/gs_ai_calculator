"""追加描述层：annotation 与 character_note 的建表与基础 CRUD。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.locking import database_lock, database_write
from genshin_db.validation import (
    ALL_STATUS,
    TARGET_TYPES,
    enum_value,
    positive_int,
    text_field,
)
from genshin_db.validation import version_line as validate_line

ANNOTATION_DDL = """
CREATE TABLE IF NOT EXISTS annotation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    version_line TEXT,
    category TEXT,
    title TEXT,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    author TEXT,
    reviewer TEXT,
    reviewed_at TEXT,
    game_version_range TEXT
);
CREATE INDEX IF NOT EXISTS idx_annotation_target ON annotation (target_type, target_id);
"""

CHARACTER_NOTE_DDL = """
CREATE TABLE IF NOT EXISTS character_note (
    character_id INTEGER NOT NULL,
    version_line TEXT NOT NULL,
    note TEXT,
    updated_at TEXT,
    PRIMARY KEY (character_id, version_line)
);
"""


@dataclass
class Annotation:
    target_type: str
    target_id: int
    content: str
    title: str = ""
    category: str = ""
    version_line: str | None = None
    status: str = "draft"
    id: int | None = None
    created_at: str = ""
    updated_at: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@database_write
def create_annotation_tables(db_path: Path) -> None:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "annotation_store.create_annotation_tables")
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(ANNOTATION_DDL)
            conn.executescript(CHARACTER_NOTE_DDL)
    finally:
        conn.close()


@database_write
def add_annotation(db_path: Path, item: Annotation) -> int:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "annotation_store.add_annotation")
    enum_value(item.target_type, "target_type", TARGET_TYPES)
    target_id = positive_int(item.target_id, "target_id")
    validate_line(item.version_line, nullable=True)
    enum_value(item.status, "status", ALL_STATUS)
    text_field(item.content, "content", required=True)
    text_field(item.title, "title")
    text_field(item.category, "category")
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            if item.target_type in ("weapon", "artifact_set", "artifact", "equip_affix"):
                table = item.target_type
                key = "affix_id" if table == "equip_affix" else "id"
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                args = [target_id]
                clause = f"{key}=?"
                if item.version_line is not None:
                    clause += " AND version_line=?"
                    args.append(item.version_line)
                if not exists or not conn.execute(f"SELECT 1 FROM {table} WHERE {clause}", args).fetchone():
                    raise ValueError(f"追加描述目标不存在: {table} #{target_id} ({item.version_line})")
            cur = conn.execute(
                """INSERT INTO annotation
                   (target_type, target_id, version_line, category, title, content, status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item.target_type, target_id, item.version_line,
                    item.category, item.title, item.content, item.status, now, now,
                ),
            )
            return cur.lastrowid
    finally:
        conn.close()


def list_annotations(
    db_path: Path, target_type: str, target_id: int, status: str = "approved"
) -> list[Annotation]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id, target_type, target_id, version_line, category, title, content,
                      status, created_at, updated_at
               FROM annotation
               WHERE target_type=? AND target_id=? AND status=?
               ORDER BY id""",
            (target_type, target_id, status),
        ).fetchall()
        return [
            Annotation(
                id=r[0], target_type=r[1], target_id=r[2], version_line=r[3],
                category=r[4] or "", title=r[5] or "", content=r[6],
                status=r[7], created_at=r[8], updated_at=r[9],
            )
            for r in rows
        ]
    finally:
        conn.close()


@database_write
def mark_orphans(db_path: Path, valid_ids: set[tuple]) -> int:
    """将目标已不存在的 annotation 标记为 status='orphan'（不删除），返回标记数。"""
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "annotation_store.mark_orphans")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, target_type, target_id, version_line FROM annotation WHERE status != 'orphan'"
        ).fetchall()
        common_ids = {key[:2] for key in valid_ids}
        orphan_ids = [
            r[0] for r in rows
            if not ((r[1], r[2]) in valid_ids or
                    (r[1], r[2], r[3]) in valid_ids or
                    (r[3] is None and (r[1], r[2]) in common_ids))
        ]
        if orphan_ids:
            with conn:
                conn.executemany(
                    "UPDATE annotation SET status='orphan', updated_at=? WHERE id=?",
                    [(_now(), i) for i in orphan_ids],
                )
        return len(orphan_ids)
    finally:
        conn.close()


@database_write
def migrate_annotations(src_db: Path, dst_db: Path) -> int:
    """把旧库的 annotation / character_note 原样迁移到新库（保留 id），返回迁移条数。"""
    from genshin_db.live_mutation_policy import assert_candidate_database

    dst_db = assert_candidate_database(dst_db, "annotation_store.migrate_annotations")
    if not src_db.exists():
        return 0
    src = sqlite3.connect(src_db)
    try:
        table_exists = src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='annotation'"
        ).fetchone()[0]
        if not table_exists:
            return 0
        annotations = src.execute("SELECT * FROM annotation ORDER BY id").fetchall()
        sequence_row = src.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='annotation'"
        ).fetchone()
        sequence = sequence_row[0] if sequence_row else 0
        notes = src.execute(
            "SELECT * FROM character_note"
        ).fetchall() if src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='character_note'"
        ).fetchone()[0] else []
    finally:
        src.close()

    with database_lock(dst_db):
        create_annotation_tables(dst_db)
        dst = sqlite3.connect(dst_db)
        try:
            _write_migrated(dst, annotations, notes, sequence)
        finally:
            dst.close()
    return len(annotations) + len(notes)


def _write_migrated(dst: sqlite3.Connection, annotations: list, notes: list, sequence: int) -> None:
    with dst:
        # 重复快照同步时必须反映源库删除，不能只做追加。
        dst.execute("DELETE FROM annotation")
        dst.execute("DELETE FROM character_note")
        if annotations:
            placeholders = ",".join("?" * len(annotations[0]))
            dst.executemany(f"INSERT INTO annotation VALUES ({placeholders})", annotations)
        if notes:
            dst.executemany("INSERT INTO character_note VALUES (?,?,?,?)", notes)
        dst.execute("DELETE FROM sqlite_sequence WHERE name='annotation'")
        dst.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('annotation',?)", (sequence,))


@database_write
def import_annotation_bundle(db_path: Path, bundle: dict, *, dry_run: bool = True) -> dict:
    """Explicit insert-only import. Stable identities live in the existing author field.

    Import never runs in B updates. Reimporting retains edited/reviewed/orphan rows;
    deleting a fact stays deleted through updates, and only explicit reimport can add
    it again. All payload validation precedes writes, which share one transaction.
    """
    from genshin_db.live_mutation_policy import assert_candidate_database
    from genshin_db.mechanic_facts import validate_bundle
    from genshin_db.targets import all_targets

    db_path = assert_candidate_database(db_path, "annotation_store.import_annotation_bundle")
    if type(dry_run) is not bool:
        raise ValueError("dry_run必须为boolean")
    bundle_id, candidates = validate_bundle(bundle)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results = []
    try:
        targets = all_targets(conn)
        with conn:
            for candidate in candidates:
                key = (candidate["target_type"], candidate["target_id"], candidate["version_line"])
                existing = conn.execute("SELECT * FROM annotation WHERE author=? ORDER BY id", (candidate["author"],)).fetchall()
                result = {"fact_id": candidate["fact_id"], "target_type": key[0], "target_id": key[1], "version_line": key[2]}
                if existing:
                    same = len(existing) == 1 and all(existing[0][k] == v for k, v in candidate.items() if k != "fact_id")
                    result.update(action="unchanged" if same else "conflict_preserved", annotation_ids=[row["id"] for row in existing])
                elif not (key in targets or key[2] is None and any(t[:2] == key[:2] for t in targets)):
                    result.update(action="target_missing", annotation_ids=[])
                else:
                    result.update(action="would_insert" if dry_run else "inserted", annotation_ids=[])
                    if not dry_run:
                        values = {k: v for k, v in candidate.items() if k != "fact_id"}
                        values.update(created_at=_now(), updated_at=_now())
                        cur = conn.execute("INSERT INTO annotation (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")", list(values.values()))
                        result["annotation_ids"] = [cur.lastrowid]
                results.append(result)
    finally:
        conn.close()
    counts = {action: sum(row["action"] == action for row in results) for action in ("would_insert", "inserted", "unchanged", "conflict_preserved", "target_missing")}
    return {"bundle_id": bundle_id, "dry_run": dry_run, "items": results, "counts": counts,
            "policy": "仅新增；同身份人工修改/审核状态保留；B更新不自动灌入事实包"}
