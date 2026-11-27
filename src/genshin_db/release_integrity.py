"""Canonical immutable-content identity for a released SQLite database.

The database contains a small, explicit set of user-editable or operational
tables.  A whole-file hash therefore cannot prove that the released game data
is unchanged after installation.  This module hashes every *other* table from
its actual schema and rows and combines those table receipts into one release
core root.

The implementation is deliberately independent from release/profile code so
the builder and installer can both recompute the same proof without trusting
metadata stored inside the database being checked.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any

RELEASE_CORE_CONTRACT = "genshin-database-release-core-v1"
RELEASE_CORE_SCHEMA_VERSION = 1
MUTABLE_LAYER_CONTRACT = "genshin-database-mutable-layer-v1"

# v1 still stores reviewed mechanism semantics in the locally editable layer.
# When base/override tables are separated, only the override/review tables stay
# here; the shipped semantic base becomes part of the immutable core.
DEFAULT_MUTABLE_TABLES = frozenset(
    {
        "annotation",
        "character_note",
        "mechanic_semantic_fact",
        "mechanic_descriptor",
        "mechanic_semantic_head",
        "mechanic_semantic_override",
        "mechanic_semantic_review",
        "import_log",
    }
)

_MUTABLE_DATA_COMPONENT = "mechanic_semantics"


class ReleaseIntegrityError(ValueError):
    """Stable validation failure for release-core callers."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _cell(value: Any) -> list[str]:
    if value is None:
        return ["null", ""]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if math.isnan(value):
            rendered = "nan"
        elif math.isinf(value):
            rendered = "+inf" if value > 0 else "-inf"
        else:
            rendered = value.hex()
        return ["real", rendered]
    raise ReleaseIntegrityError("database_release_core_sqlite_type_unsupported")


def _schema_receipt(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    objects = [
        {"type": row[0], "name": row[1], "sql": row[2]}
        for row in connection.execute(
            """SELECT type,name,sql FROM sqlite_schema
               WHERE tbl_name=? AND type IN ('table','index','trigger')
               ORDER BY type,name""",
            (table,),
        )
    ]
    columns = [
        list(row)
        for row in connection.execute(f"PRAGMA table_xinfo({_quote(table)})")
    ]
    foreign_keys = sorted(
        (list(row) for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})")),
        key=_canonical,
    )
    indexes = []
    for row in connection.execute(f"PRAGMA index_list({_quote(table)})"):
        index_name = row[1]
        indexes.append(
            {
                "index_list": list(row),
                "index_xinfo": [
                    list(item)
                    for item in connection.execute(
                        f"PRAGMA index_xinfo({_quote(index_name)})"
                    )
                ],
            }
        )
    indexes.sort(key=_canonical)
    return {
        "objects": objects,
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
    }


def _content_receipt(
    connection: sqlite3.Connection,
    table: str,
    *,
    where_sql: str | None = None,
    parameters: tuple[Any, ...] = (),
) -> tuple[int, str]:
    row_hashes: list[bytes] = []
    count = 0
    query = f"SELECT * FROM {_quote(table)}"
    if where_sql is not None:
        query += " WHERE " + where_sql
    cursor = connection.execute(query, parameters)
    for row in cursor:
        count += 1
        row_hashes.append(_row_hash(row))
    return count, _rows_hash(row_hashes)


def _row_hash(row):
    return hashlib.sha256(_canonical([_cell(value) for value in row])).digest()


def _rows_hash(row_hashes):
    digest = hashlib.sha256()
    digest.update(b"genshin-database-table-content-v1\0")
    digest.update(str(len(row_hashes)).encode("ascii"))
    digest.update(b"\0")
    for row_hash in sorted(row_hashes):
        digest.update(row_hash)
    return digest.hexdigest()


def meta_row_witness(connection, key):
    """Read a metadata value plus the sibling digests needed to prove its receipt."""
    siblings = []
    value = None
    for row in connection.execute("SELECT * FROM meta LIMIT 1025"):
        if row[0] == key:
            value = row[1]
        else:
            siblings.append(_row_hash(row).hex())
    if len(siblings) > 1023:
        raise ValueError("数据库 meta 行数超出证明范围。")
    return value, sorted(siblings)


def verify_meta_row_witness(key, value, siblings, receipt):
    """Prove the exact raw value against a previously admitted meta-table receipt."""
    if (type(siblings) is not list or len(siblings) > 1023
            or receipt.get("row_count") != len(siblings) + 1):
        return False
    try:
        hashes = [bytes.fromhex(item) for item in siblings
                  if isinstance(item, str) and len(item) == 64]
    except ValueError:
        return False
    return (len(hashes) == len(siblings) and all(len(item) == 32 for item in hashes)
            and _rows_hash([*hashes, _row_hash((key, value))]) == receipt.get("content_sha256"))


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_schema
               WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'
               ORDER BY name"""
        )
    ]


def build_release_core(
    database: Path | sqlite3.Connection,
    *,
    mutable_tables: Iterable[str] = DEFAULT_MUTABLE_TABLES,
) -> dict[str, Any]:
    """Recompute the immutable release core from actual SQLite schema/rows."""

    mutable = sorted(set(mutable_tables))
    if any(type(name) is not str or not name or len(name) > 128 for name in mutable):
        raise ReleaseIntegrityError("database_release_core_mutable_tables_invalid")
    if isinstance(database, sqlite3.Connection):
        context = nullcontext(database)
    else:
        path = Path(database).resolve()
        context = closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    with context as connection:
        tables: dict[str, dict[str, Any]] = {}
        for table in _table_names(connection):
            schema = _schema_receipt(connection, table)
            if table in mutable:
                # Rows in these tables are deliberately user/operation mutable,
                # but their table/index/trigger contract is not.  Keep the
                # actual schema in the immutable release core and represent the
                # intentionally excluded row set with the canonical empty-table
                # content receipt.  ``mutable_tables`` is a code-owned whitelist,
                # so a verifier can distinguish this from a genuinely empty
                # immutable table without trusting a manifest-controlled flag.
                row_count, content_sha256 = _content_receipt(
                    connection,
                    table,
                    where_sql="0",
                )
            elif table == "data_component":
                # Only the reviewed mechanic-semantics component is a local
                # manual layer.  Equipment/runtime rows are startup authority
                # and must remain in the immutable proof.
                row_count, content_sha256 = _content_receipt(
                    connection,
                    table,
                    where_sql="component IS NULL OR component <> ?",
                    parameters=(_MUTABLE_DATA_COMPONENT,),
                )
            else:
                row_count, content_sha256 = _content_receipt(connection, table)
            tables[table] = {
                "schema_sha256": _sha(schema),
                "row_count": row_count,
                "content_sha256": content_sha256,
            }
    body = {
        "contract": RELEASE_CORE_CONTRACT,
        "mutable_tables": mutable,
        "tables": tables,
    }
    return {**body, "root_sha256": _sha(body)}


def build_mutable_layer_fingerprint(
    database: Path | sqlite3.Connection,
    *,
    mutable_tables: Iterable[str] = DEFAULT_MUTABLE_TABLES,
) -> str:
    """Fingerprint actual schema and rows in the explicit mutable layer.

    This proof is separate from ``release_core``: changing a reviewed
    annotation or override must not make an otherwise valid release look
    corrupted, while receipts still need the exact local manual-layer identity
    that existed when they were written.
    """

    mutable = sorted(set(mutable_tables))
    if any(type(name) is not str or not name or len(name) > 128 for name in mutable):
        raise ReleaseIntegrityError("database_release_core_mutable_tables_invalid")
    if isinstance(database, sqlite3.Connection):
        context = nullcontext(database)
    else:
        path = Path(database).resolve()
        context = closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    with context as connection:
        available = set(_table_names(connection))
        tables: dict[str, dict[str, Any]] = {}
        for table in mutable:
            if table not in available:
                continue
            schema = _schema_receipt(connection, table)
            row_count, content_sha256 = _content_receipt(connection, table)
            tables[table] = {
                "schema_sha256": _sha(schema),
                "row_count": row_count,
                "content_sha256": content_sha256,
            }
        if "data_component" in available:
            schema = _schema_receipt(connection, "data_component")
            row_count, content_sha256 = _content_receipt(
                connection,
                "data_component",
                where_sql="component = ?",
                parameters=(_MUTABLE_DATA_COMPONENT,),
            )
            tables["data_component#mechanic_semantics"] = {
                "schema_sha256": _sha(schema),
                "row_count": row_count,
                "content_sha256": content_sha256,
            }
    return _sha(
        {
            "contract": MUTABLE_LAYER_CONTRACT,
            "mutable_tables": mutable,
            "tables": tables,
        }
    )


def validate_release_core(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "contract",
        "mutable_tables",
        "tables",
        "root_sha256",
    }:
        raise ReleaseIntegrityError("database_release_core_invalid")
    if value["contract"] != RELEASE_CORE_CONTRACT:
        raise ReleaseIntegrityError("database_release_core_unsupported")
    mutable = value["mutable_tables"]
    if (
        type(mutable) is not list
        or mutable != sorted(set(mutable))
        or any(type(name) is not str or not name or len(name) > 128 for name in mutable)
    ):
        raise ReleaseIntegrityError("database_release_core_mutable_tables_invalid")
    if mutable != sorted(DEFAULT_MUTABLE_TABLES):
        # The whitelist is part of this contract version's trusted code, not a
        # manifest-controlled escape hatch for silently excluding game tables.
        raise ReleaseIntegrityError("database_release_core_mutable_tables_unsupported")
    tables = value["tables"]
    if type(tables) is not dict or list(tables) != sorted(tables):
        raise ReleaseIntegrityError("database_release_core_tables_invalid")
    normalized_tables: dict[str, dict[str, Any]] = {}
    for table, receipt in tables.items():
        if type(table) is not str or not table or len(table) > 128 or type(receipt) is not dict:
            raise ReleaseIntegrityError("database_release_core_tables_invalid")
        if set(receipt) != {"schema_sha256", "row_count", "content_sha256"}:
            raise ReleaseIntegrityError("database_release_core_tables_invalid")
        schema_sha = receipt["schema_sha256"]
        content_sha = receipt["content_sha256"]
        if any(
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in (schema_sha, content_sha)
        ):
            raise ReleaseIntegrityError("database_release_core_tables_invalid")
        row_count = receipt["row_count"]
        if type(row_count) is not int or row_count < 0:
            raise ReleaseIntegrityError("database_release_core_tables_invalid")
        normalized_tables[table] = {
            "schema_sha256": schema_sha,
            "row_count": row_count,
            "content_sha256": content_sha,
        }
    normalized = {
        "contract": RELEASE_CORE_CONTRACT,
        "mutable_tables": list(mutable),
        "tables": normalized_tables,
    }
    root = value["root_sha256"]
    if (
        type(root) is not str
        or len(root) != 64
        or any(character not in "0123456789abcdef" for character in root)
        or root != _sha(normalized)
    ):
        raise ReleaseIntegrityError("database_release_core_root_invalid")
    return {**normalized, "root_sha256": root}


def database_matches_release_core(database: Path, expected: Mapping[str, Any]) -> bool:
    """Return whether actual immutable schema/rows match a validated receipt."""

    normalized = validate_release_core(dict(expected))
    actual = build_release_core(database, mutable_tables=normalized["mutable_tables"])
    return actual == normalized
