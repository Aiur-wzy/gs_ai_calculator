"""Revisioned, explicitly reviewed mechanism data; never installs on a read.

Connection helpers do not commit the caller's transaction. Path writers use the
same database lock as B publication. The upstream and annotation layers are not
modified by this component.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.locking import database_lock
from genshin_db.validation import version_line as validate_line

COMPONENT = "mechanic_semantics"
SCHEMA_VERSION = 1
LIMITS = {"max_import_records": 100, "max_import_bytes": 2 * 1024 * 1024,
          "max_catalog_items": 50}
TABLES = {"fact": "mechanic_semantic_fact", "descriptor": "mechanic_descriptor"}
HEAD_TABLE = "mechanic_semantic_head"
RECORD_FIELDS = ("id", "revision", "subject", "payload", "evidence", "applicability", "review")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COLUMNS = ("version_line", "record_id", "revision", "schema_version", "subject_json",
            "payload_json", "evidence_json", "applicability_json", "status", "reviewer",
            "reviewed_at", "created_at", "updated_at", "payload_fingerprint", "review_fingerprint")


class SemanticStoreError(ValueError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _identifier(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name}必须为1–80位小写标识")
    return value


def _revision(value, *, nullable=False):
    if nullable and value is None:
        return value
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise ValueError("revision必须为1..2^31-1的整数")
    return value


def _kind(value):
    if not isinstance(value, str) or value not in TABLES:
        raise ValueError("record_kind必须为fact或descriptor")
    return value


def _exists(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone())


def validate_component(conn, version_line=None):
    """Read-only preflight, including all lines' future-version protection."""
    if version_line is not None:
        validate_line(version_line)
    rows = conn.execute(
        "SELECT version_line,schema_version FROM data_component WHERE component=? ORDER BY version_line",
        (COMPONENT,),
    ).fetchall() if _exists(conn, "data_component") else []
    for line, version in rows:
        validate_line(line)
        if type(version) is not int or version != SCHEMA_VERSION:
            raise SemanticStoreError("semantic_schema_unsupported", "不支持此机制语义组件版本")
    present = [_exists(conn, table) for table in (*TABLES.values(), HEAD_TABLE)]
    if any(present) and not all(present) or rows and not all(present):
        raise SemanticStoreError("semantic_dataset_invalid", "机制语义组件缺表")
    if all(present):
        for table in TABLES.values():
            names = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
            if names != _COLUMNS:
                raise SemanticStoreError("semantic_dataset_invalid", "机制语义表列不匹配")
        head_names = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({HEAD_TABLE})"))
        if head_names != ("version_line", "record_kind", "record_id", "revision", "updated_at"):
            raise SemanticStoreError("semantic_dataset_invalid", "机制语义头表列不匹配")
        registered = {line for line, _ in rows}
        for table in (*TABLES.values(), HEAD_TABLE):
            if any(row[0] not in registered for row in conn.execute(f"SELECT DISTINCT version_line FROM {table}")):
                raise SemanticStoreError("semantic_dataset_invalid", "语义记录缺组件登记")
        for kind, table in TABLES.items():
            if conn.execute(f"SELECT 1 FROM {HEAD_TABLE} h WHERE h.record_kind=? AND NOT EXISTS "
                            f"(SELECT 1 FROM {table} r WHERE r.version_line=h.version_line AND r.record_id=h.record_id "
                            "AND r.revision=h.revision) LIMIT 1", (kind,)).fetchone():
                raise SemanticStoreError("semantic_dataset_invalid", "语义头指向不存在的修订")
    available = bool(rows) if version_line is None else any(line == version_line for line, _ in rows)
    return {"component": COMPONENT, "schema_version": SCHEMA_VERSION,
            "status": "ready" if available else "missing",
            "reason": None if available else "component_missing"}


def ensure_schema(conn, *, version_line="release"):
    """Install only this component. Caller owns transaction/commit and write lock."""
    validate_line(version_line)
    validate_component(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS data_component (component TEXT,version_line TEXT,"
                 "schema_version INTEGER,upstream_commit TEXT,built_at TEXT,validation_json TEXT,"
                 "PRIMARY KEY(component,version_line))")
    for table in TABLES.values():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ("
                     "version_line TEXT NOT NULL CHECK(version_line IN ('release','beta')),"
                     "record_id TEXT NOT NULL,revision INTEGER NOT NULL,schema_version INTEGER NOT NULL,"
                     "subject_json TEXT NOT NULL,payload_json TEXT NOT NULL,evidence_json TEXT NOT NULL,"
                     "applicability_json TEXT NOT NULL,status TEXT NOT NULL,reviewer TEXT,reviewed_at TEXT,"
                     "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload_fingerprint TEXT NOT NULL,"
                     "review_fingerprint TEXT NOT NULL,PRIMARY KEY(version_line,record_id,revision))")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {HEAD_TABLE} ("
                 "version_line TEXT NOT NULL CHECK(version_line IN ('release','beta')),"
                 "record_kind TEXT NOT NULL CHECK(record_kind IN ('fact','descriptor')),"
                 "record_id TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL,"
                 "PRIMARY KEY(version_line,record_kind,record_id))")
    conn.execute("INSERT OR IGNORE INTO data_component VALUES (?,?,?,?,?,?)",
                 (COMPONENT, version_line, SCHEMA_VERSION, None, datetime.now(UTC).isoformat(),
                  _dump({"schema_version": SCHEMA_VERSION})))
    return validate_component(conn, version_line)


def semantic_datasets(conn):
    """Describe availability without installing or approving semantic data.

    Validation deliberately retains the component's cross-line compatibility
    guard. Counts describe stored revisions, not executable mechanisms.
    """
    lines = {}
    for line in ("release", "beta"):
        state = {"state": "missing", "schema_version": None,
                 "counts": {"facts": 0, "descriptors": 0, "heads": 0},
                 "execution_support": "not_evaluated"}
        try:
            if _exists(conn, "data_component"):
                row = conn.execute("SELECT schema_version FROM data_component WHERE component=? AND version_line=?",
                                   (COMPONENT, line)).fetchone()
                if row:
                    state["schema_version"] = row[0]
            status = validate_component(conn, line)
            state["state"] = status["status"]
            if status["status"] == "ready":
                for name, table in (("facts", TABLES["fact"]), ("descriptors", TABLES["descriptor"]),
                                    ("heads", HEAD_TABLE)):
                    state["counts"][name] = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE version_line=?", (line,),
                    ).fetchone()[0]
        except (sqlite3.DatabaseError, ValueError, TypeError) as error:
            reason = getattr(error, "reason", "semantic_dataset_invalid")
            state.update(state="incompatible" if reason == "semantic_schema_unsupported" else "invalid",
                         counts=None, diagnostic={"reason": reason, "message": str(error)})
        lines[line] = state
    return {COMPONENT: {"schema_version": SCHEMA_VERSION, "lines": lines}}


def _validated(record, kind):
    from genshin_runtime.mechanism_semantics import (
        record_payload_fingerprint,
        record_review_fingerprint,
        validate_record,
    )

    normalized = validate_record(record, kind)
    return normalized, record_payload_fingerprint(normalized, kind), record_review_fingerprint(normalized, kind)


def _decode(row, kind):
    values = dict(zip(_COLUMNS, row, strict=True))
    record = {"id": values["record_id"], "revision": values["revision"],
              **{key: json.loads(values[key + "_json"])
                 for key in ("subject", "payload", "evidence", "applicability")},
              "review": {key: values[key] for key in ("status", "reviewer", "reviewed_at")}}
    normalized, payload_fp, review_fp = _validated(record, kind)
    if (values["schema_version"] != SCHEMA_VERSION
            or normalized["applicability"]["version_line"] != values["version_line"]
            or payload_fp != values["payload_fingerprint"] or review_fp != values["review_fingerprint"]):
        raise SemanticStoreError("semantic_dataset_invalid", "语义记录指纹或版本不一致")
    return {**normalized, "payload_fingerprint": payload_fp, "review_fingerprint": review_fp,
            "storage": {key: values[key] for key in ("version_line", "created_at", "updated_at")}}


def _head(conn, line, kind, ident):
    if not _exists(conn, HEAD_TABLE):
        return None
    row = conn.execute(f"SELECT revision FROM {HEAD_TABLE} WHERE version_line=? AND record_kind=? AND record_id=?",
                       (line, kind, ident)).fetchone()
    return row[0] if row else None


def read_record(conn, version_line, record_kind, record_id, revision=None):
    validate_line(version_line)
    try:
        kind = _kind(record_kind)
        _identifier(record_id, "record_id")
        _revision(revision, nullable=True)
    except ValueError as exc:
        raise SemanticStoreError("semantic_record_request_invalid", str(exc)) from exc
    component = validate_component(conn, version_line)
    if component["status"] != "ready":
        return component
    if revision is None:
        revision = _head(conn, version_line, kind, record_id)
        if revision is None:
            return {"status": "missing", "reason": "head_missing"}
    row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE version_line=? AND record_id=? AND revision=?",
                       (version_line, record_id, revision)).fetchone()
    if row is None:
        return {"status": "missing", "reason": "record_missing"}
    return _decode(row, kind)


def read_current_reviewed_record(conn, version_line, record_kind, record_id, expected_revision=None):
    """Read current review qualification inside the caller's active transaction.

    This does not admit a source, dependency graph, or executable mechanism.
    Explicit historical revisions remain available through ``read_record``.
    No transaction, schema, review state, or head is created or modified here.
    """
    validate_line(version_line)
    kind = _kind(record_kind)
    _identifier(record_id, "record_id")
    _revision(expected_revision, nullable=True)
    if not conn.in_transaction:
        raise SemanticStoreError("semantic_transaction_required", "当前审核读取需要调用者已开启同一事务")
    result = {"scope": "current_semantic_record_review", "execution_authority": False,
              "source_status": "not_checked", "record": None, "receipt": None,
              "review_status": None, "current_revision": None}
    component = validate_component(conn, version_line)
    if component["status"] != "ready":
        return {**result, "status": "missing", "reason": component["reason"]}
    heads = conn.execute(
        f"SELECT revision,updated_at FROM {HEAD_TABLE} WHERE version_line=? AND record_kind=? AND record_id=? LIMIT 2",
        (version_line, kind, record_id),
    ).fetchall()
    if not heads:
        return {**result, "status": "missing", "reason": "head_missing"}
    if len(heads) != 1:
        raise SemanticStoreError("semantic_dataset_invalid", "当前语义头身份不唯一")
    revision, updated_at = heads[0]
    if (type(revision) is not int or not 1 <= revision <= 2**31 - 1
            or not isinstance(updated_at, str) or not 1 <= len(updated_at) <= 80):
        raise SemanticStoreError("semantic_dataset_invalid", "当前语义头身份或元数据无效")
    result["current_revision"] = revision
    if expected_revision is not None and expected_revision != revision:
        return {**result, "status": "pending", "reason": "head_changed"}
    rows = conn.execute(
        f"SELECT * FROM {TABLES[kind]} WHERE version_line=? AND record_id=? AND revision=? LIMIT 2",
        (version_line, record_id, revision),
    ).fetchall()
    if len(rows) != 1:
        raise SemanticStoreError("semantic_dataset_invalid", "当前语义修订缺失或身份不唯一")
    # A damaged same-column table may lack its PK or store schema_version as
    # REAL. Equality alone would accept 1.0 as schema 1; this admission is strict.
    if type(rows[0][3]) is not int or rows[0][3] != SCHEMA_VERSION:
        raise SemanticStoreError("semantic_dataset_invalid", "当前语义记录存储版本必须为精确整数")
    try:
        # Reuse the canonical decoder and both stored fingerprint checks.
        # This connection's existing transaction binds the head and record read.
        record = _decode(rows[0], kind)
    except SemanticStoreError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticStoreError("semantic_dataset_invalid", "当前语义记录格式无效") from exc
    result["review_status"] = record["review"]["status"]
    result["receipt"] = {"version_line": version_line, "record_kind": kind, "record_id": record_id,
                         "revision": revision, "head_updated_at": updated_at,
                         "payload_fingerprint": record["payload_fingerprint"],
                         "review_fingerprint": record["review_fingerprint"]}
    if result["review_status"] != "approved":
        return {**result, "status": "pending", "reason": "review_not_approved"}
    return {**result, "status": "ready", "reason": None, "record": record}


def read_catalog(conn, version_line="release", *, target_type=None, target_id=None, page=1, page_size=20):
    validate_line(version_line)
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= LIMITS["max_catalog_items"]:
        raise ValueError("分页参数无效")
    if (target_type is None) != (target_id is None):
        raise ValueError("target_type/target_id必须成对")
    if target_type is not None and (not isinstance(target_type, str) or not target_type or type(target_id) is not int or not 1 <= target_id <= 2**53 - 1):
        raise ValueError("目标身份无效")
    component = validate_component(conn, version_line)
    if component["status"] != "ready":
        return {**component, "items": [], "total": 0, "page": page, "page_size": page_size}
    clause = "h.version_line=? AND h.record_kind='descriptor'"
    args = [version_line]
    if target_type is not None:
        clause += " AND json_extract(d.subject_json,'$.target_type')=? AND json_extract(d.subject_json,'$.target_id')=?"
        args += [target_type, target_id]
    sql = (f"FROM {HEAD_TABLE} h JOIN {TABLES['descriptor']} d ON d.version_line=h.version_line "
           f"AND d.record_id=h.record_id AND d.revision=h.revision WHERE {clause}")
    total = conn.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]
    rows = conn.execute("SELECT d.* " + sql + " ORDER BY h.record_id LIMIT ? OFFSET ?",
                        [*args, page_size, (page - 1) * page_size]).fetchall()
    items = []
    for row in rows:
        record = _decode(row, "descriptor")
        items.append({key: record[key] for key in ("id", "revision", "subject", "review", "payload_fingerprint", "review_fingerprint")})
    return {**component, "items": items, "total": total, "page": page, "page_size": page_size}


@contextmanager
def _write(db_path):
    path = Path(db_path).resolve()
    with database_lock(path):
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _bundle(bundle):
    if type(bundle) is not dict or set(bundle) != {"schema_version", "id", "facts", "descriptors", "heads"}:
        raise ValueError("语义包字段必须为schema_version/id/facts/descriptors/heads")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != SCHEMA_VERSION:
        raise ValueError("语义包schema_version必须为1")
    _identifier(bundle["id"], "bundle.id")
    if any(type(bundle[key]) is not list for key in ("facts", "descriptors", "heads")):
        raise ValueError("语义包记录必须为数组")
    if not 1 <= sum(len(bundle[key]) for key in ("facts", "descriptors", "heads")) <= LIMITS["max_import_records"]:
        raise ValueError("语义包必须包含1–100条记录/头操作")
    try:
        size = len(_dump(bundle).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("语义包必须为有限JSON") from exc
    if size > LIMITS["max_import_bytes"]:
        raise ValueError("语义包超过2MiB")
    records, seen = [], set()
    for kind, field in (("fact", "facts"), ("descriptor", "descriptors")):
        for item in bundle[field]:
            record, payload_fp, review_fp = _validated(item, kind)
            key = (kind, record["id"], record["revision"])
            if key in seen:
                raise ValueError("语义包记录主键重复")
            seen.add(key)
            records.append((kind, record, payload_fp, review_fp))
    heads, seen = [], set()
    for item in bundle["heads"]:
        if type(item) is not dict or set(item) != {"record_kind", "record_id", "revision", "expected_head_revision"}:
            raise ValueError("语义头字段无效；必须显式携带expected_head_revision")
        kind = _kind(item["record_kind"])
        ident = _identifier(item["record_id"], "head.record_id")
        _revision(item["revision"], nullable=True)
        _revision(item["expected_head_revision"], nullable=True)
        if (kind, ident) in seen:
            raise ValueError("语义头操作重复")
        seen.add((kind, ident))
        heads.append(copy.deepcopy(item))
    return records, heads


def import_bundle(db_path, bundle, *, version_line="release", dry_run=True):
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "semantic_store.import_bundle")
    validate_line(version_line)
    if type(dry_run) is not bool:
        raise ValueError("dry_run必须为boolean")
    records, heads = _bundle(bundle)
    if any(row["applicability"]["version_line"] != version_line for _, row, _, _ in records):
        raise ValueError("语义记录适用版本线与导入版本线不符")
    with _write(db_path) as conn:
        component = validate_component(conn, version_line)
        available = component["status"] == "ready"
        actions, conflicts = [], []
        planned = {(kind, row["id"], row["revision"]): row for kind, row, _, _ in records}
        for kind, row, _, _ in records:
            old = read_record(conn, version_line, kind, row["id"], row["revision"]) if available else {"status": "missing"}
            action = "insert"
            if old.get("status") != "missing":
                action = "unchanged" if _dump({key: old[key] for key in RECORD_FIELDS}) == _dump(row) else "conflict_preserved"
            item = {"record_kind": kind, "record_id": row["id"], "revision": row["revision"], "action": action}
            actions.append(item)
            if action == "conflict_preserved":
                conflicts.append(item)
            if action == "insert" and available:
                exists = conn.execute(f"SELECT 1 FROM {TABLES[kind]} WHERE version_line=? AND record_id=? LIMIT 1",
                                      (version_line, row["id"])).fetchone()
                if exists and not any(h["record_kind"] == kind and h["record_id"] == row["id"] for h in heads):
                    raise ValueError("新增已有资料的修订必须携带对应head CAS")
        for item in heads:
            current = _head(conn, version_line, item["record_kind"], item["record_id"])
            inserted = any(a["action"] == "insert" and a["record_kind"] == item["record_kind"]
                           and a["record_id"] == item["record_id"] for a in actions)
            if current != item["expected_head_revision"] and (current != item["revision"] or inserted):
                conflicts.append({**item, "action": "head_conflict", "actual_head_revision": current})
            elif item["revision"] is not None:
                key = (item["record_kind"], item["record_id"], item["revision"])
                if key not in planned and (not available or read_record(conn, version_line, *key).get("status") == "missing"):
                    raise ValueError("语义头引用不存在的修订")
        for kind, row, _, _ in records:
            if kind == "descriptor":
                for ref in row["payload"]["descriptor"]["evidence_fact_ids"]:
                    ident, revision = ref.rsplit("@", 1)
                    key = ("fact", ident, int(revision))
                    if key not in planned and (not available or read_record(conn, version_line, *key).get("status") == "missing"):
                        raise ValueError("机制描述引用不存在的事实修订")
        applied = not dry_run and not conflicts
        if applied:
            ensure_schema(conn, version_line=version_line)
            now = datetime.now(UTC).isoformat()
            for (kind, row, payload_fp, review_fp), action in zip(records, actions, strict=True):
                if action["action"] != "insert":
                    continue
                values = (version_line, row["id"], row["revision"], SCHEMA_VERSION,
                          *(_dump(row[key]) for key in ("subject", "payload", "evidence", "applicability")),
                          *(row["review"][key] for key in ("status", "reviewer", "reviewed_at")),
                          now, now, payload_fp, review_fp)
                conn.execute(f"INSERT INTO {TABLES[kind]} VALUES ({','.join('?' for _ in values)})", values)
            for item in heads:
                key = (version_line, item["record_kind"], item["record_id"])
                if item["revision"] == _head(conn, *key):
                    continue
                conn.execute(f"DELETE FROM {HEAD_TABLE} WHERE version_line=? AND record_kind=? AND record_id=?", key)
                if item["revision"] is not None:
                    conn.execute(f"INSERT INTO {HEAD_TABLE} VALUES (?,?,?,?,?)", (*key, item["revision"], now))
        return {"component": COMPONENT, "bundle_id": bundle["id"], "version_line": version_line,
                "dry_run": dry_run, "applied": applied, "status": "conflict_preserved" if conflicts else "ready",
                "records": actions, "heads": heads, "conflicts": conflicts}


def review_record(db_path, *, version_line, record_kind, record_id, revision, status,
                  reviewer, reviewed_at, expected_review_fingerprint):
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "semantic_store.review_record")
    validate_line(version_line)
    _kind(record_kind)
    _identifier(record_id, "record_id")
    _revision(revision)
    if not isinstance(expected_review_fingerprint, str) or not _SHA.fullmatch(expected_review_fingerprint):
        raise ValueError("expected_review_fingerprint必须为SHA256")
    with _write(db_path) as conn:
        current = read_record(conn, version_line, record_kind, record_id, revision)
        if current.get("status") == "missing":
            raise SemanticStoreError("semantic_record_missing", "待审核语义修订不存在")
        if current["review_fingerprint"] != expected_review_fingerprint:
            raise SemanticStoreError("semantic_review_conflict", "审核状态已变化，拒绝覆盖")
        record = {key: current[key] for key in RECORD_FIELDS}
        record["review"] = {"status": status, "reviewer": reviewer, "reviewed_at": reviewed_at}
        _, _, review_fp = _validated(record, record_kind)
        if review_fp != current["review_fingerprint"]:
            conn.execute(f"UPDATE {TABLES[record_kind]} SET status=?,reviewer=?,reviewed_at=?,updated_at=?,review_fingerprint=? "
                         "WHERE version_line=? AND record_id=? AND revision=?",
                         (status, reviewer, reviewed_at, datetime.now(UTC).isoformat(), review_fp,
                          version_line, record_id, revision))
        return read_record(conn, version_line, record_kind, record_id, revision)


def _snapshot(conn):
    validate_component(conn)
    data = {}
    for table in (*TABLES.values(), HEAD_TABLE):
        if _exists(conn, table):
            order = "version_line,record_kind,record_id" if table == HEAD_TABLE else "version_line,record_id,revision"
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            if table != HEAD_TABLE:
                kind = next(key for key, name in TABLES.items() if name == table)
                for row in rows:
                    _decode(row, kind)
            data[table] = [list(row) for row in rows]
        else:
            data[table] = []
    data["data_component"] = [list(row) for row in conn.execute(
        "SELECT * FROM data_component WHERE component=? ORDER BY version_line", (COMPONENT,)
    )] if _exists(conn, "data_component") else []
    for line, kind, ident, revision, _ in data[HEAD_TABLE]:
        if not any(row[0:3] == [line, ident, revision] for row in data[TABLES[_kind(kind)]]):
            raise SemanticStoreError("semantic_dataset_invalid", "语义头指向不存在的修订")
    return data


def semantic_digest(conn):
    data = _snapshot(conn)
    return {"fingerprint": _sha(data), "counts": {key: len(rows) for key, rows in data.items()}}


def synchronize(src_conn, dst_conn):
    """Exact two-line mirror, including revocations and deleted heads.

    B must hold the live database lock and its source read transaction. Destination
    changes use a savepoint and do not commit the surrounding publication work.
    """
    data = _snapshot(src_conn)
    validate_component(dst_conn)
    if not data["data_component"]:
        current = _snapshot(dst_conn)
        if any(current[table] for table in (*TABLES.values(), HEAD_TABLE)):
            raise SemanticStoreError("semantic_sync_conflict", "源缺组件但候选含语义记录，拒绝保留未知数据")
        return {"status": "source_missing", **semantic_digest(dst_conn)}
    dst_conn.execute("SAVEPOINT semantic_synchronize")
    try:
        lines = [row[1] for row in data["data_component"]]
        for line in lines:
            ensure_schema(dst_conn, version_line=line)
        for table in (*TABLES.values(), HEAD_TABLE):
            if _exists(dst_conn, table):
                dst_conn.execute(f"DELETE FROM {table}")
                rows = data[table]
                if rows:
                    dst_conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in rows[0])})", rows)
        if _exists(dst_conn, "data_component"):
            dst_conn.execute("DELETE FROM data_component WHERE component=?", (COMPONENT,))
            dst_conn.executemany("INSERT INTO data_component VALUES (?,?,?,?,?,?)", data["data_component"])
        result = semantic_digest(dst_conn)
        if result["fingerprint"] != _sha(data):
            raise SemanticStoreError("semantic_sync_mismatch", "语义同步摘要不一致")
        dst_conn.execute("RELEASE semantic_synchronize")
        return result
    except BaseException:
        dst_conn.execute("ROLLBACK TO semantic_synchronize")
        dst_conn.execute("RELEASE semantic_synchronize")
        raise
