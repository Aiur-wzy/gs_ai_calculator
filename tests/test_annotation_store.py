"""annotation_store 模块测试：建表幂等、增查、orphan 标记、迁移。"""

import sqlite3

from genshin_db import annotation_store as astore


def test_migration_preserves_deleted_id_sequence(tmp_path):
    from genshin_db.queries import delete_annotation

    old, new = tmp_path / "old.db", tmp_path / "new.db"
    astore.create_annotation_tables(old)
    ann_id = astore.add_annotation(old, astore.Annotation("character", 1, "deleted"))
    delete_annotation(old, ann_id)
    astore.migrate_annotations(old, new)
    assert astore.add_annotation(new, astore.Annotation("character", 1, "new")) > ann_id


def test_create_tables_idempotent(tmp_path):
    db = tmp_path / "a.db"
    astore.create_annotation_tables(db)
    astore.create_annotation_tables(db)  # 二次调用不报错
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = [r[1] for r in conn.execute("PRAGMA table_info(annotation)")]
    conn.close()
    assert {"annotation", "character_note"} <= tables
    assert {"author", "reviewer", "reviewed_at", "game_version_range"} <= set(cols)


def test_add_and_list(tmp_path):
    db = tmp_path / "a.db"
    astore.create_annotation_tables(db)
    id1 = astore.add_annotation(db, astore.Annotation(
        target_type="skill", target_id=10075, category="ICD",
        title="标准附着", content="2.5s/3hits 标准 ICD", status="approved",
    ))
    id2 = astore.add_annotation(db, astore.Annotation(
        target_type="skill", target_id=10075, content="未审核条目",
    ))
    assert id2 == id1 + 1

    approved = astore.list_annotations(db, "skill", 10075)
    assert len(approved) == 1 and approved[0].title == "标准附着"
    drafts = astore.list_annotations(db, "skill", 10075, status="draft")
    assert len(drafts) == 1
    other = astore.list_annotations(db, "skill", 99999)
    assert other == []


def test_mark_orphans(tmp_path):
    db = tmp_path / "a.db"
    astore.create_annotation_tables(db)
    astore.add_annotation(db, astore.Annotation(target_type="character", target_id=10000002, content="存在"))
    astore.add_annotation(db, astore.Annotation(target_type="skill", target_id=99999, content="已删除"))
    marked = astore.mark_orphans(db, {("character", 10000002)})
    assert marked == 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT target_id, status FROM annotation ORDER BY id").fetchall()
    conn.close()
    assert rows[0][1] != "orphan"
    assert rows[1] == (99999, "orphan")
    # 重复标记幂等
    assert astore.mark_orphans(db, {("character", 10000002)}) == 0


def test_migrate_annotations(tmp_path):
    old = tmp_path / "old.db"
    new = tmp_path / "new.db"
    astore.create_annotation_tables(old)
    astore.add_annotation(old, astore.Annotation(
        target_type="talent", target_id=151, content="一命减防", status="approved",
    ))
    n = astore.migrate_annotations(old, new)
    assert n == 1
    kept = astore.list_annotations(new, "talent", 151)
    assert len(kept) == 1 and kept[0].content == "一命减防" and kept[0].id == 1

    # 旧库不存在/无表时返回 0
    assert astore.migrate_annotations(tmp_path / "no.db", tmp_path / "n2.db") == 0
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert astore.migrate_annotations(empty, tmp_path / "n3.db") == 0
