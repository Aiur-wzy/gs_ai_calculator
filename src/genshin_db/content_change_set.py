"""Reviewed project-content changes applied only to a release candidate.

The upstream projection and the project-maintained knowledge layer have
different owners.  This module handles only the latter: annotations, character
appendices and reviewed mechanic-semantic rows.  A change set is a complete,
digest-bound declaration of the rows that may change.  It is never a live
database write API.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_db.live_mutation_policy import assert_candidate_database
from genshin_db.publication_evidence import TABLE as PUBLICATION_TABLE

CHANGE_SET_CONTRACT = "genshin-database-content-change-set-v1"
APPROVAL_CONTRACT = "genshin-database-content-change-approval-v1"
APPLICATION_RECEIPT_CONTRACT = "genshin-database-content-application-receipt-v1"
SCHEMA_VERSION = 1

_SHA256 = frozenset("0123456789abcdef")
_SOURCE_KINDS = frozenset(
    {
        "appendix",
        "manual_curated",
        "community_contribution",
        "reviewed_rule_resource",
        "entity_publication_evidence",
    }
)
_ACTIONS = frozenset({"add", "update", "delete"})
_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    PUBLICATION_TABLE: ("entity_type", "entity_id", "version_line"),
    "annotation": ("id",),
    "character_note": ("character_id", "version_line"),
    "mechanic_semantic_fact": ("version_line", "record_id", "revision"),
    "mechanic_descriptor": ("version_line", "record_id", "revision"),
    "mechanic_semantic_head": ("version_line", "record_kind", "record_id"),
    "mechanic_semantic_override": (
        "version_line",
        "record_kind",
        "record_id",
        "revision",
    ),
    "mechanic_semantic_review": (
        "version_line",
        "record_kind",
        "record_id",
        "revision",
    ),
    "data_component": ("component", "version_line"),
}
MAX_SOURCES = 256
MAX_OPERATIONS = 20_000


class ContentChangeSetError(ValueError):
    """Stable rejection for invalid or stale content-release inputs."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise ContentChangeSetError(reason)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _SHA256 for character in value)
    )


def _short_text(value: Any, *, limit: int = 4096) -> bool:
    return type(value) is str and 0 < len(value) <= limit


def _quote(identifier: str) -> str:
    # Identifiers only come from the code-owned allowlist or SQLite metadata.
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    if not _table_exists(connection, table):
        return ()
    return tuple(row[1] for row in connection.execute(f"PRAGMA table_xinfo({_quote(table)})"))


def _project_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return a deterministic snapshot of every currently supported project row."""

    tables: dict[str, Any] = {}
    for table in sorted(_TABLE_KEYS):
        columns = _columns(connection, table)
        if not columns:
            if table == PUBLICATION_TABLE:
                # Preserve the fingerprint of pre-publication releases and
                # their already approved content changes.
                continue
            tables[table] = {"columns": [], "rows": []}
            continue
        query = f"SELECT * FROM {_quote(table)}"
        parameters: tuple[Any, ...] = ()
        if table == "data_component":
            query += " WHERE component=?"
            parameters = ("mechanic_semantics",)
        rows = [dict(zip(columns, row, strict=True)) for row in connection.execute(query, parameters)]
        rows.sort(key=_canonical)
        tables[table] = {"columns": list(columns), "rows": rows}
    return {"contract": "genshin-project-content-snapshot-v1", "tables": tables}


def project_content_fingerprint(database: str | Path | sqlite3.Connection) -> str:
    """Hash the project-maintained rows, independently from upstream data."""

    if isinstance(database, sqlite3.Connection):
        return _digest(_project_snapshot(database))
    path = Path(database).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        return _digest(_project_snapshot(connection))


def _validate_source(value: Any) -> dict[str, Any]:
    required = {"source_id", "kind", "locator", "evidence_sha256", "summary"}
    if type(value) is not dict or set(value) != required:
        _fail("database_content_source_invalid")
    if (
        not _short_text(value["source_id"], limit=256)
        or value["kind"] not in _SOURCE_KINDS
        or not _short_text(value["locator"])
        or not _is_sha256(value["evidence_sha256"])
        or not _short_text(value["summary"])
    ):
        _fail("database_content_source_invalid")
    return copy.deepcopy(value)


def _validate_operation(value: Any, source_ids: set[str]) -> dict[str, Any]:
    required = {
        "operation_id",
        "source_id",
        "table",
        "action",
        "key",
        "before_sha256",
        "row",
    }
    if type(value) is not dict or set(value) != required:
        _fail("database_content_operation_invalid")
    table = value["table"]
    action = value["action"]
    if (
        not _short_text(value["operation_id"], limit=256)
        or value["source_id"] not in source_ids
        or table not in _TABLE_KEYS
        or action not in _ACTIONS
        or type(value["key"]) is not dict
        or set(value["key"]) != set(_TABLE_KEYS[table])
    ):
        _fail("database_content_operation_invalid")
    if table == "data_component" and value["key"].get("component") != "mechanic_semantics":
        _fail("database_content_operation_table_not_project_owned")
    before = value["before_sha256"]
    row = value["row"]
    if action == "add":
        if before is not None or type(row) is not dict:
            _fail("database_content_operation_invalid")
    elif action == "update":
        if not _is_sha256(before) or type(row) is not dict:
            _fail("database_content_operation_invalid")
    elif not _is_sha256(before) or row is not None:
        _fail("database_content_operation_invalid")
    if row is not None and any(row.get(name) != value["key"][name] for name in _TABLE_KEYS[table]):
        _fail("database_content_operation_key_mismatch")
    if table == PUBLICATION_TABLE and row is not None:
        from genshin_db.publication_evidence import validate_evidence_row

        try:
            validate_evidence_row(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise ContentChangeSetError("database_content_publication_evidence_invalid") from exc
    normalized = copy.deepcopy(value)
    normalized["key"] = {name: value["key"][name] for name in _TABLE_KEYS[table]}
    return normalized


def validate_change_set(value: Any) -> dict[str, Any]:
    required = {
        "contract",
        "schema_version",
        "base_database_release_id",
        "target_database_release_id",
        "base_project_content_fingerprint",
        "sources",
        "operations",
        "impact",
        "change_set_id",
    }
    if type(value) is not dict or set(value) != required:
        _fail("database_content_change_set_invalid")
    if value["contract"] != CHANGE_SET_CONTRACT or value["schema_version"] != SCHEMA_VERSION:
        _fail("database_content_change_set_unsupported")
    if (
        not _short_text(value["base_database_release_id"], limit=256)
        or not _short_text(value["target_database_release_id"], limit=256)
        or value["base_database_release_id"] == value["target_database_release_id"]
        or not _is_sha256(value["base_project_content_fingerprint"])
    ):
        _fail("database_content_change_set_invalid")
    sources_value = value["sources"]
    operations_value = value["operations"]
    if (
        type(sources_value) is not list
        or not sources_value
        or len(sources_value) > MAX_SOURCES
        or type(operations_value) is not list
        or len(operations_value) > MAX_OPERATIONS
    ):
        _fail("database_content_change_set_invalid")
    sources = [_validate_source(item) for item in sources_value]
    source_ids = [item["source_id"] for item in sources]
    if len(source_ids) != len(set(source_ids)):
        _fail("database_content_source_duplicate")
    operations = [_validate_operation(item, set(source_ids)) for item in operations_value]
    operation_ids = [item["operation_id"] for item in operations]
    targets = [(item["table"], _canonical(item["key"])) for item in operations]
    if len(operation_ids) != len(set(operation_ids)):
        _fail("database_content_operation_duplicate")
    if len(targets) != len(set(targets)):
        _fail("database_content_operation_target_duplicate")
    impact = value["impact"]
    if (
        type(impact) is not dict
        or set(impact) != {"components", "summary"}
        or type(impact["components"]) is not list
        or impact["components"] != sorted(set(impact["components"]))
        or any(not _short_text(item, limit=128) for item in impact["components"])
        or not _short_text(impact["summary"])
    ):
        _fail("database_content_change_set_invalid")
    body = {
        "contract": CHANGE_SET_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "base_database_release_id": value["base_database_release_id"],
        "target_database_release_id": value["target_database_release_id"],
        "base_project_content_fingerprint": value["base_project_content_fingerprint"],
        "sources": sources,
        "operations": operations,
        "impact": copy.deepcopy(impact),
    }
    if not _is_sha256(value["change_set_id"]) or value["change_set_id"] != _digest(body):
        _fail("database_content_change_set_digest_mismatch")
    return {**body, "change_set_id": value["change_set_id"]}


def create_change_set(
    *,
    base_database_release_id: str,
    target_database_release_id: str,
    base_project_content_fingerprint: str,
    sources: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    impact: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "contract": CHANGE_SET_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "base_database_release_id": base_database_release_id,
        "target_database_release_id": target_database_release_id,
        "base_project_content_fingerprint": base_project_content_fingerprint,
        "sources": copy.deepcopy(sources),
        "operations": copy.deepcopy(operations),
        "impact": copy.deepcopy(impact),
    }
    return validate_change_set({**body, "change_set_id": _digest(body)})


def validate_approval(value: Any, change_set: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract",
        "schema_version",
        "change_set_id",
        "base_database_release_id",
        "target_database_release_id",
        "approved_by",
        "approved_at",
        "approval_digest",
    }
    if type(value) is not dict or set(value) != required:
        _fail("database_content_approval_invalid")
    body = {key: copy.deepcopy(value[key]) for key in required - {"approval_digest"}}
    if (
        body["contract"] != APPROVAL_CONTRACT
        or body["schema_version"] != SCHEMA_VERSION
        or body["change_set_id"] != change_set["change_set_id"]
        or body["base_database_release_id"] != change_set["base_database_release_id"]
        or body["target_database_release_id"] != change_set["target_database_release_id"]
        or not _short_text(body["approved_by"], limit=256)
        or not _short_text(body["approved_at"], limit=128)
        or not _is_sha256(value["approval_digest"])
        or value["approval_digest"] != _digest(body)
    ):
        _fail("database_content_approval_invalid")
    return {**body, "approval_digest": value["approval_digest"]}


def create_approval(
    change_set: Mapping[str, Any], *, approved_by: str, approved_at: str
) -> dict[str, Any]:
    checked = validate_change_set(dict(change_set))
    body = {
        "contract": APPROVAL_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "change_set_id": checked["change_set_id"],
        "base_database_release_id": checked["base_database_release_id"],
        "target_database_release_id": checked["target_database_release_id"],
        "approved_by": approved_by,
        "approved_at": approved_at,
    }
    return validate_approval({**body, "approval_digest": _digest(body)}, checked)


def _where(key: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    return " AND ".join(f"{_quote(name)}=?" for name in key), tuple(key.values())


def _read_row(
    connection: sqlite3.Connection, table: str, key: Mapping[str, Any]
) -> dict[str, Any] | None:
    columns = _columns(connection, table)
    if not columns:
        _fail("database_content_candidate_table_missing")
    clause, parameters = _where(key)
    rows = connection.execute(
        f"SELECT * FROM {_quote(table)} WHERE {clause}", parameters
    ).fetchall()
    if len(rows) > 1:
        _fail("database_content_candidate_key_not_unique")
    return dict(zip(columns, rows[0], strict=True)) if rows else None


def _validate_planned_rows(
    connection: sqlite3.Connection, operations: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], tuple[str, ...], dict[str, Any] | None]]:
    planned = []
    for operation in operations:
        table = operation["table"]
        columns = _columns(connection, table)
        if not columns:
            _fail("database_content_candidate_table_missing")
        row = operation["row"]
        if row is not None and set(row) != set(columns):
            _fail("database_content_operation_row_schema_mismatch")
        current = _read_row(connection, table, operation["key"])
        action = operation["action"]
        if action == "add":
            if current is not None:
                _fail("database_content_add_conflict")
        else:
            if current is None or _digest(current) != operation["before_sha256"]:
                _fail("database_content_before_mismatch")
        planned.append((operation, columns, current))
    return planned


def apply_content_change_set(
    *,
    base_database: str | Path,
    candidate_database: str | Path,
    change_set: Mapping[str, Any],
    approval: Mapping[str, Any],
    base_database_release_id: str,
    target_database_release_id: str,
    applied_at: str | None = None,
) -> dict[str, Any]:
    """Apply one approved declaration to an isolated complete candidate.

    The candidate must initially contain exactly the base release's project
    rows.  Upstream-derived tables may already differ and are not inspected or
    changed by this function.
    """

    checked = validate_change_set(dict(change_set))
    checked_approval = validate_approval(dict(approval), checked)
    if (
        base_database_release_id != checked["base_database_release_id"]
        or target_database_release_id != checked["target_database_release_id"]
    ):
        _fail("database_content_release_binding_mismatch")
    base = Path(base_database).resolve()
    candidate = assert_candidate_database(
        candidate_database, "content_change_set.apply_content_change_set"
    )
    if not base.is_file() or not candidate.is_file() or base.samefile(candidate):
        _fail("database_content_candidate_invalid")
    base_fingerprint = project_content_fingerprint(base)
    if base_fingerprint != checked["base_project_content_fingerprint"]:
        _fail("database_content_base_fingerprint_mismatch")

    with closing(sqlite3.connect(candidate)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = project_content_fingerprint(connection)
            if before != base_fingerprint:
                _fail("database_content_candidate_base_drift")
            publication_changed = any(op["table"] == PUBLICATION_TABLE for op in checked["operations"])
            if publication_changed:
                from genshin_db.publication_evidence import SCHEMA

                connection.execute(SCHEMA)
            planned = _validate_planned_rows(connection, checked["operations"])
            counts = {"add": 0, "update": 0, "delete": 0}
            for operation, columns, _current in planned:
                table = operation["table"]
                action = operation["action"]
                clause, parameters = _where(operation["key"])
                if action == "delete":
                    cursor = connection.execute(
                        f"DELETE FROM {_quote(table)} WHERE {clause}", parameters
                    )
                elif action == "add":
                    row = operation["row"]
                    cursor = connection.execute(
                        f"INSERT INTO {_quote(table)} "
                        f"({','.join(_quote(name) for name in columns)}) VALUES "
                        f"({','.join('?' for _ in columns)})",
                        tuple(row[name] for name in columns),
                    )
                else:
                    row = operation["row"]
                    updated = [name for name in columns if name not in operation["key"]]
                    cursor = connection.execute(
                        f"UPDATE {_quote(table)} SET "
                        + ",".join(f"{_quote(name)}=?" for name in updated)
                        + f" WHERE {clause}",
                        tuple(row[name] for name in updated) + parameters,
                    )
                if cursor.rowcount != 1:
                    _fail("database_content_operation_cardinality_mismatch")
                counts[action] += 1
            if publication_changed:
                from genshin_db.publication_evidence import (
                    validate_publication_inventory,
                )

                try:
                    validate_publication_inventory(connection)
                except ValueError as exc:
                    raise ContentChangeSetError("database_content_publication_inventory_invalid") from exc
            after_snapshot = _project_snapshot(connection)
            after = _digest(after_snapshot)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    body = {
        "contract": APPLICATION_RECEIPT_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "change_set_id": checked["change_set_id"],
        "approval_digest": checked_approval["approval_digest"],
        "base_database_release_id": base_database_release_id,
        "target_database_release_id": target_database_release_id,
        "before_project_content_fingerprint": before,
        "after_project_content_fingerprint": after,
        "operation_counts": counts,
        "applied_at": applied_at or datetime.now(UTC).isoformat(),
    }
    return {**body, "receipt_fingerprint": _digest(body)}


def row_fingerprint(row: Mapping[str, Any]) -> str:
    """Public helper for authoring exact update/delete preconditions."""

    if not isinstance(row, Mapping):
        _fail("database_content_operation_invalid")
    return _digest(dict(row))


__all__ = [
    "APPLICATION_RECEIPT_CONTRACT",
    "APPROVAL_CONTRACT",
    "CHANGE_SET_CONTRACT",
    "ContentChangeSetError",
    "apply_content_change_set",
    "create_approval",
    "create_change_set",
    "project_content_fingerprint",
    "row_fingerprint",
    "validate_approval",
    "validate_change_set",
]
