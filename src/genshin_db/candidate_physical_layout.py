"""Read-only journal admission for physical-only candidate publication."""
from __future__ import annotations

import sqlite3
from contextlib import closing


def assert_rollback_database(path, fail) -> None:
    def sidecars_present():
        return any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm"))

    if sidecars_present():
        raise fail("读取优化拒绝有WAL/SHM旁文件的数据库")
    with path.open("rb") as handle:
        header = handle.read(20)
    if len(header) >= 20 and (header[18] == 2 or header[19] == 2):
        raise fail("读取优化仅支持回滚日志，拒绝WAL模式")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] not in ("delete", "truncate", "persist"):
            raise fail("读取优化仅支持回滚日志，拒绝WAL模式")
    if sidecars_present():
        raise fail("读取优化数据库出现WAL/SHM旁文件，拒绝替换")


def discard_unpublished_database(path) -> bool:
    """Do not unlink a database whose live WAL reader may still own sidecars."""
    if any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm")):
        return False
    path.unlink(missing_ok=True)
    return True
