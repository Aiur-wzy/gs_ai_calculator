"""Read entity release evidence without interpreting skills or granting execution authority."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

ENTITY_TYPES = ("character", "weapon", "artifact_set")
STATUS = frozenset({"released", "unreleased", "unknown", "conflicting"})
SOURCE_KINDS = frozenset({"official_reference", "community_reference", "upstream_raw",
                         "project_review", "user_confirmation"})
TABLE = "entity_publication"
SCHEMA = """CREATE TABLE IF NOT EXISTS entity_publication (
    entity_type TEXT NOT NULL CHECK(entity_type IN ('character', 'weapon', 'artifact_set')),
    entity_id INTEGER NOT NULL CHECK(entity_id > 0),
    version_line TEXT NOT NULL CHECK(version_line IN ('release', 'beta')),
    entity_status TEXT NOT NULL CHECK(entity_status IN ('released', 'unreleased', 'unknown', 'conflicting')),
    checked_on TEXT NOT NULL,
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, version_line)
) WITHOUT ROWID"""


def table_exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def publication_fingerprint(conn):
    """Invalidate cached context on entity evidence changes alone."""
    if not table_exists(conn):
        return None
    digest = hashlib.sha256(b"genshin-entity-publication-v1\n")
    for row in conn.execute(f"SELECT * FROM {TABLE} ORDER BY entity_type,entity_id,version_line"):
        digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_evidence_row(row):
    """Validate exact entity-only inputs at the reviewed change boundary."""
    from datetime import date
    if row.get("entity_type") not in ENTITY_TYPES or type(row.get("entity_id")) is not int or row["entity_id"] <= 0:
        raise ValueError("invalid_publication_subject")
    if row.get("entity_status") not in STATUS or row.get("version_line") not in {"release", "beta"}:
        raise ValueError("invalid_publication_claim")
    if date.fromisoformat(row["checked_on"]).isoformat() != row["checked_on"]:
        raise ValueError("invalid_publication_date")
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("reason", "scope")):
        raise ValueError("invalid_publication_scope")
    evidence = json.loads(row["evidence_json"])
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("invalid_publication_evidence")
    for source in evidence:
        if (not isinstance(source, dict) or source.get("source_kind") not in SOURCE_KINDS
                or not isinstance(source.get("origin"), str) or not source["origin"].strip()
                or not isinstance(source.get("assessment"), str) or not source["assessment"].strip()):
            raise ValueError("invalid_publication_evidence")
    if row["entity_status"] in {"released", "unreleased"} and all(
            source["source_kind"] == "upstream_raw" for source in evidence):
        raise ValueError("publication_raw_snapshot_not_release_proof")
    return evidence


def validate_publication_inventory(conn):
    """Check that the release preserves one audited row for every curated entity."""
    totals = Counter()
    for kind in ENTITY_TYPES:
        # These table names are fixed by ENTITY_TYPES.
        expected = set(conn.execute(f'SELECT id,version_line FROM "{kind}"').fetchall())
        actual = set()
        for values in conn.execute(f"SELECT * FROM {TABLE} WHERE entity_type=?", (kind,)):
            row = dict(zip(("entity_type", "entity_id", "version_line", "entity_status", "checked_on",
                            "scope", "reason", "evidence_json"), values, strict=True))
            validate_evidence_row(row)
            actual.add((row["entity_id"], row["version_line"]))
            totals[row["entity_status"]] += 1
        if actual != {tuple(item) for item in expected}:
            raise ValueError(f"publication_inventory_mismatch:{kind}")
    return dict(sorted(totals.items()))


def publication_by_id(conn, kind, ids, line, *, detailed=False):
    """A bounded indexed read for only the entities in the current request."""
    if kind not in ENTITY_TYPES:
        raise ValueError("invalid_publication_subject")
    unique = tuple(dict.fromkeys(ids))
    if not unique:
        return {}
    records = {}
    if table_exists(conn):
        placeholders = ','.join('?' for _ in unique)
        records = {row[0]: row[1:] for row in conn.execute(
            f"SELECT entity_id,entity_status,checked_on,scope,reason,evidence_json FROM {TABLE} "
            f"WHERE entity_type=? AND version_line=? AND entity_id IN ({placeholders})", (kind, line, *unique))}
    result = {}
    for ident in unique:
        record = records.get(ident)
        status = record[0] if record else "unknown"
        note = {
            "released": "该实体已有正式上线证据。",
            "unreleased": "该实体尚未正式上线。实际采用前请向用户确认。",
            "unknown": "目前尚无法核实该实体是否正式上线。实际采用前请向用户确认。",
            "conflicting": "该实体的上线证据存在冲突。实际采用前请向用户确认。",
        }[status]
        row = {"entity": {"type": kind, "id": ident}, "version_line": line, "entity_status": status,
               "evidence_state": "audited" if record else "no_evidence", "requires_usage_confirmation": status != "released",
               "usage_notice": note}
        if detailed:
            row.update(checked_on=record[1] if record else None, scope=record[2] if record else None,
                       reason=record[3] if record else "本数据库没有该实体的上线证据记录。",
                       evidence=json.loads(record[4]) if record else [])
        result[ident] = row
    return result


def owner_publication(conn, scope, line, *, detailed=False):
    """Reuse existing ownership. A skill or effect never receives its own status."""
    ids = ("character_ids", "weapon_ids", "artifact_set_ids")
    return [row for kind, key in zip(ENTITY_TYPES, ids, strict=True)
            for row in publication_by_id(conn, kind, scope.get(key, ()), line, detailed=detailed).values()]
