"""Optional physical indexes; never part of a runtime source identity."""
from __future__ import annotations

import hashlib
import sqlite3
import struct

from genshin_db.runtime_catalog import RuntimeConfigError

INDEX_NAME = "idx_runtime_physical_ref_target_cover_v1"
INDEX_TABLE = "runtime_reference"
INDEX_COLUMNS = ("version_line", "resolved_definition_id", "resolution_status",
                 "target_kind", "target_key_json")
INDEX_SQL = f"CREATE INDEX {INDEX_NAME} ON {INDEX_TABLE}({','.join(INDEX_COLUMNS)})"


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def missing_physical_indexes(conn):
    """Inspect only; an absent index is compatible, a same-name impostor is not."""
    obj = conn.execute("SELECT type,name,tbl_name FROM sqlite_master WHERE name=? COLLATE NOCASE", (INDEX_NAME,)).fetchone()
    if obj is None:
        return (INDEX_NAME,)
    if tuple(obj) != ("index", INDEX_NAME, INDEX_TABLE):
        raise RuntimeConfigError("读取优化索引同名对象或目标表不符", "physical_index_invalid", 500)
    entries = [tuple(row) for row in conn.execute(f"PRAGMA index_list({_quote(INDEX_TABLE)})")]
    entry = next((row for row in entries if row[1] == INDEX_NAME), None)
    columns = [tuple(row) for row in conn.execute(f"PRAGMA index_xinfo({_quote(INDEX_NAME)})")]
    keys = [row for row in columns if row[5] == 1]
    if (entry is None or entry[2:] != (0, "c", 0)
            or tuple(row[2] for row in keys) != INDEX_COLUMNS
            or any(row[1] < 0 or row[3] != 0 or row[4] != "BINARY" for row in keys)):
        raise RuntimeConfigError("读取优化索引规格不符（列序、排序、校对、唯一或条件）", "physical_index_invalid", 500)
    return ()


def ensure_physical_indexes(conn):
    """Write only the approved DDL; caller owns transaction and publication."""
    missing = missing_physical_indexes(conn)
    if missing:
        conn.execute(INDEX_SQL)
    if missing_physical_indexes(conn):
        raise RuntimeConfigError("读取优化索引创建后校验失败", "physical_index_invalid", 500)
    return missing


def _feed_value(digest, value):
    # Type tags distinguish INTEGER 1, REAL 1.0, TEXT '1', BLOB and NULL.
    if value is None:
        tag, data = b"n", b""
    elif type(value) is int:
        tag, data = b"i", str(value).encode("ascii")
    elif type(value) is float:
        tag, data = b"f", struct.pack(">d", value)
    elif isinstance(value, str):
        tag, data = b"s", value.encode("utf-8")
    elif isinstance(value, bytes):
        tag, data = b"b", value
    else:
        raise RuntimeConfigError("物理优化保全核对遇到未知SQLite值类型")
    digest.update(tag)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def preservation_manifest(conn):
    """Stream all table values deterministically; only the approved index is omitted.

    No administrative exceptions are applied here: import_log and sqlite_sequence
    must also match before the caller appends the new success log.
    """
    missing_physical_indexes(conn)
    schema = [tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name!=? ORDER BY type,name,tbl_name,sql",
        (INDEX_NAME,))]
    tables = {}
    for kind, name, _, _sql in schema:
        if kind != "table":
            continue
        columns = [tuple(row) for row in conn.execute(f"PRAGMA table_xinfo({_quote(name)})")]
        names = [row[1] for row in columns]
        fields = ",".join(_quote(column) for column in names)
        # A physical clone/index build preserves rowid. Prefer it to sorting BLOBs.
        aliases = [candidate for candidate in ("rowid", "_rowid_", "oid")
                   if candidate.lower() not in {column.lower() for column in names}]
        rowid = None
        if aliases:
            try:
                conn.execute(f"SELECT {_quote(aliases[0])} FROM {_quote(name)} LIMIT 0")
                # A quoted missing name is a string on SQLite; unquoted proves rowid.
                conn.execute(f"SELECT {aliases[0]} FROM {_quote(name)} LIMIT 0")
                rowid = aliases[0]
            except sqlite3.OperationalError:
                pass
        if rowid is not None:
            fields = rowid + "," + fields
            order = rowid
        else:
            order = ",".join(f"typeof({_quote(column)}),{_quote(column)} COLLATE BINARY" for column in names)
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(f"SELECT {fields} FROM {_quote(name)} ORDER BY {order}"):
            digest.update(b"r")
            for value in row:
                _feed_value(digest, value)
            count += 1
        tables[name] = {"columns": columns, "rowid_included": rowid is not None,
                        "rows": count, "sha256": digest.hexdigest()}
    return {"schema": schema, "tables": tables}
