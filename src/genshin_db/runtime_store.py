"""Compressed same-snapshot runtime sources and exact structural indexes."""
from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
import zlib
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.equipment import ensure_schema as ensure_equipment_schema
from genshin_db.locking import database_write
from genshin_db.runtime_catalog import (
    GADGET_EXCEL_PATH,
    GLOBAL_COMBAT_PATH,
    INDEXER_VERSION,
    MATERIAL_EXCEL_PATH,
    MAX_SOURCE_BYTES,
    PROFILE_ID,
    PROFILE_VERSION,
    SCHEMA_VERSION,
    SOURCE_CAPABILITY_CONTRACT,
    SUPPORTED_INDEXER_VERSIONS,
    RuntimeConfigError,
    dumps,
    enumerate_runtime_sources,
    extract_definitions,
    extract_material_references,
    extract_object_references,
    extract_parameter_bindings,
    extract_references,
    fingerprint,
    parse_source,
    resolve_pointer,
    snapshot_id,
    walk,
)
from genshin_db.runtime_physical_index import ensure_physical_indexes
from genshin_db.validation import version_line as validate_line
from genshin_runtime.normalization import for_commit, verify_receipt

COMPONENT = "runtime_config"
TABLE_COLUMNS = {
    "runtime_source": "source_path TEXT,source_family TEXT,upstream_commit TEXT,source_sha256 TEXT,source_size_bytes INTEGER,payload_codec TEXT,payload_blob BLOB,json_root_type TEXT,indexed_definition_count INTEGER,source_order INTEGER,imported_at TEXT",
    "runtime_definition": "definition_id TEXT,definition_kind TEXT,exact_key_json TEXT,owner_definition_id TEXT,source_path TEXT,json_pointer TEXT,variant_key TEXT,namespace_key TEXT,node_sha256 TEXT,summary_json TEXT",
    "runtime_reference": "reference_id TEXT,from_definition_id TEXT,source_path TEXT,json_pointer TEXT,field_name TEXT,relation_kind TEXT,target_kind TEXT,target_key_json TEXT,scope_json TEXT,source_order INTEGER,presence_status TEXT,resolution_status TEXT,resolved_definition_id TEXT,candidate_ids_json TEXT,extraction_rule_id TEXT",
    "runtime_parameter_binding": "binding_id TEXT,origin_definition_id TEXT,operation_pointer TEXT,operation_index INTEGER,operation_type TEXT,target_reference_id TEXT,parameter_name TEXT,raw_expression_json TEXT,extraction_status TEXT",
    "runtime_object_reference": "reference_id TEXT,from_definition_id TEXT,source_path TEXT,json_pointer TEXT,field_name TEXT,relation_kind TEXT,target_kind TEXT,target_key_json TEXT,scope_json TEXT,source_order INTEGER,presence_status TEXT,resolution_status TEXT,resolved_definition_id TEXT,candidate_ids_json TEXT,extraction_rule_id TEXT,legacy_reference_id TEXT",
    "runtime_material_reference": "reference_id TEXT,from_definition_id TEXT,source_path TEXT,json_pointer TEXT,field_name TEXT,relation_kind TEXT,target_kind TEXT,target_key_json TEXT,scope_json TEXT,source_order INTEGER,presence_status TEXT,resolution_status TEXT,resolved_definition_id TEXT,candidate_ids_json TEXT,extraction_rule_id TEXT,legacy_reference_id TEXT",
}
KEYS = {"runtime_source": "source_path", "runtime_definition": "definition_id",
        "runtime_reference": "reference_id", "runtime_parameter_binding": "binding_id",
        "runtime_object_reference": "reference_id", "runtime_material_reference": "reference_id"}
REQUIRED_TABLES = {*TABLE_COLUMNS.keys() - {"runtime_object_reference", "runtime_material_reference"}, "data_component", "source_manifest"}
_UNSET = object()


def tables_for_indexer(indexer_version):
    return tuple(t for t in TABLE_COLUMNS
                 if (t != "runtime_object_reference" or indexer_version in ("5", "6", "7"))
                 and (t != "runtime_material_reference" or indexer_version in ("6", "7")))


def _normalization_for_summary(summary, commit):
    """Only a pinned, built-in profile may interpret the stored raw sources."""
    if not isinstance(summary, dict):
        raise RuntimeConfigError("运行配置组件回执必须为对象")
    indexer = summary.get("indexer_version")
    if indexer not in SUPPORTED_INDEXER_VERSIONS:
        raise RuntimeConfigError("运行配置索引器版本不受支持", "unsupported_schema", 409)
    receipt = summary.get("normalization")
    if indexer == "2":
        if "normalization" in summary:
            raise RuntimeConfigError("旧运行索引不支持归一化回执")
        return None
    try:
        profile = for_commit(commit)
        if profile is None:
            if "normalization" in summary:
                raise RuntimeConfigError("当前commit没有受支持的归一化档案")
            return None
        if not isinstance(receipt, dict):
            raise RuntimeConfigError("运行配置缺少归一化回执")
        verified = verify_receipt(receipt, commit=commit)
        if receipt != profile.receipt(commit):
            raise RuntimeConfigError("运行配置归一化回执与内置档案不符")
        return verified
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("运行配置归一化回执无效: " + str(exc)) from exc


def runtime_normalization(conn, line):
    """Read the current transaction's profile once for a source traversal."""
    row = conn.execute("SELECT upstream_commit,validation_json FROM data_component WHERE component=? AND version_line=?", (COMPONENT, line)).fetchone()
    if row is None:
        raise RuntimeConfigError("运行配置组件缺失")
    try:
        summary = json.loads(row[1])
        return _normalization_for_summary(summary, row[0]), summary["indexer_version"]
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("运行配置组件回执无效") from exc


def _normalized_snapshot(line, commit, manifest, profile):
    source_snapshot = snapshot_id(line, commit, manifest)
    if profile is None:
        return source_snapshot
    return fingerprint({"source_snapshot": source_snapshot, "normalization": profile.receipt(commit)})


def ensure_schema(conn):
    ensure_equipment_schema(conn)
    for table, columns in TABLE_COLUMNS.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns},version_line TEXT NOT NULL,snapshot_id TEXT NOT NULL,PRIMARY KEY(version_line,{KEYS[table]}))")
    for suffix, table, columns in (
        ("lookup", "runtime_definition", "definition_kind,exact_key_json"),
        ("source", "runtime_definition", "source_path,json_pointer"),
        ("owner", "runtime_definition", "owner_definition_id"),
        ("from", "runtime_reference", "from_definition_id"),
        ("refsource", "runtime_reference", "source_path"),
        ("binding", "runtime_parameter_binding", "origin_definition_id"),
        ("object_from", "runtime_object_reference", "from_definition_id"),
        ("object_legacy", "runtime_object_reference", "legacy_reference_id"),
        ("object_target", "runtime_object_reference", "target_kind,target_key_json"),
        ("material_from", "runtime_material_reference", "from_definition_id"),
        ("material_legacy", "runtime_material_reference", "legacy_reference_id"),
        ("material_target", "runtime_material_reference", "target_kind,target_key_json"),
    ):
        # Runtime reads already pin a transaction/snapshot and commonly filter by line + key.
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_runtime_v2_{suffix} ON {table}(version_line,{columns},snapshot_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_v4_exact ON runtime_definition"
                 "(version_line,definition_kind,exact_key_json,source_path,json_pointer)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_v5_object_identity ON runtime_object_reference"
                 "(version_line,from_definition_id,json_pointer,relation_kind)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_v6_material_identity ON runtime_material_reference"
                 "(version_line,from_definition_id,json_pointer,relation_kind)")
    ensure_physical_indexes(conn)


def decode_source_bytes(row):
    size = row["source_size_bytes"]
    if type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES or row["payload_codec"] != "zlib":
        raise RuntimeConfigError("运行源编码或长度无效")
    try:
        decoder = zlib.decompressobj()
        payload = decoder.decompress(row["payload_blob"], size + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(payload) != size:
            raise RuntimeConfigError("运行源解压长度/数据边界无效")
    except zlib.error as exc:
        raise RuntimeConfigError(f"运行源压缩数据损坏: {exc}") from exc
    if hashlib.sha256(payload).hexdigest() != row["source_sha256"]:
        raise RuntimeConfigError("运行源SHA不匹配")
    return payload


def decode_source(row):
    return parse_source(decode_source_bytes(row))


def _insert(conn, table, row):
    keys = list(row)
    conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", [row[k] for k in keys])


def resolve_reference(row, candidates):
    """Resolve only the exact declared key, preferring an existing local modifier scope."""
    if row["resolution_status"] != "unresolved":
        return row["resolution_status"], None, []
    owner = json.loads(row["scope_json"])["owner_definition_id"]
    local = [v for v in candidates if owner and v["owner_definition_id"] == owner]
    identifiers = sorted(v["definition_id"] for v in (local or candidates))
    state = "resolved" if len(identifiers) == 1 else "ambiguous" if identifiers else "missing"
    return state, identifiers[0] if len(identifiers) == 1 else None, identifiers


def resolve_object_reference(row, candidates):
    """No owner/path/Default preference may hide an ambiguous object identity."""
    if row["resolution_status"] != "unresolved":
        return row["resolution_status"], None, []
    identifiers = sorted(v["definition_id"] for v in candidates)
    state = "resolved" if len(identifiers) == 1 else "ambiguous" if identifiers else "missing"
    return state, identifiers[0] if len(identifiers) == 1 else None, identifiers


def resolve_material_reference(row, candidates):
    """A material ID resolves exactly; no path/owner/first-candidate preference."""
    return resolve_object_reference(row, candidates)


def validate_definition_material_references(conn, line, definition, payload, *, index=None,
                                            normalization=_UNSET, indexer_version=None):
    """Re-extract the isolated material graph against complete source nodes."""
    if normalization is _UNSET or indexer_version is None:
        normalization, indexer_version = runtime_normalization(conn, line)
    if indexer_version not in ("6", "7"):
        raise RuntimeConfigError("Material精确来源索引需要重建到版本6", "material_index_upgrade_required", 409)
    expected = extract_material_references({**dict(definition), "payload": payload},
                                           normalization=normalization, indexer_version=indexer_version)
    actual = [dict(r) for r in conn.execute("SELECT * FROM runtime_material_reference WHERE version_line=? AND from_definition_id=? ORDER BY source_order,reference_id", (line, definition["definition_id"]))]
    if len(actual) != len(expected):
        raise RuntimeConfigError("runtime物品引用数量与原始定义不符")
    for stored, raw in zip(actual, expected, strict=True):
        candidates = index.get((raw["target_kind"], raw["target_key_json"]), []) if index is not None else [dict(r) for r in conn.execute("SELECT definition_id FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?", (line, raw["target_kind"], raw["target_key_json"]))]
        status, target, identifiers = resolve_material_reference(raw, candidates)
        raw.update(resolution_status=status, resolved_definition_id=target, candidate_ids_json=dumps(identifiers))
        if stored["snapshot_id"] != definition["snapshot_id"] or any(stored[key] != value for key, value in raw.items()):
            raise RuntimeConfigError("runtime物品引用结构、精确目标或作用域与原始定义不符")
    return actual


def validate_definition_object_references(conn, line, definition, payload, *, index=None,
                                          normalization=_UNSET, indexer_version=None):
    """Verify the isolated v5 object graph against complete source nodes."""
    if normalization is _UNSET or indexer_version is None:
        normalization, indexer_version = runtime_normalization(conn, line)
    if indexer_version not in ("5", "6", "7"):
        raise RuntimeConfigError("Gadget精确来源索引需要重建到版本5", "gadget_index_upgrade_required", 409)
    expected = extract_object_references({**dict(definition), "payload": payload},
                                         normalization=normalization, indexer_version=indexer_version)
    actual = [dict(r) for r in conn.execute("SELECT * FROM runtime_object_reference WHERE version_line=? AND from_definition_id=? ORDER BY source_order,reference_id", (line, definition["definition_id"]))]
    if len(actual) != len(expected):
        raise RuntimeConfigError("runtime对象引用数量与原始定义不符")
    for stored, raw in zip(actual, expected, strict=True):
        candidates = index.get((raw["target_kind"], raw["target_key_json"]), []) if index is not None else [dict(r) for r in conn.execute("SELECT definition_id FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?", (line, raw["target_kind"], raw["target_key_json"]))]
        status, target, identifiers = resolve_object_reference(raw, candidates)
        raw.update(resolution_status=status, resolved_definition_id=target, candidate_ids_json=dumps(identifiers))
        if stored["snapshot_id"] != definition["snapshot_id"] or any(stored[key] != value for key, value in raw.items()):
            raise RuntimeConfigError("runtime对象引用结构、精确目标或作用域与原始定义不符")
    return actual


def validate_definition_references(conn, line, definition, payload, *, index=None,
                                   normalization=_UNSET, indexer_version=None):
    """Re-extract a visited node, proving its edges and exact target scope against source bytes."""
    if normalization is _UNSET or indexer_version is None:
        normalization, indexer_version = runtime_normalization(conn, line)
    expected = extract_references({**dict(definition), "payload": payload},
                                  normalization=normalization, indexer_version=indexer_version)
    actual = [dict(r) for r in conn.execute("SELECT * FROM runtime_reference WHERE version_line=? AND from_definition_id=? ORDER BY source_order,reference_id", (line, definition["definition_id"]))]
    if len(actual) != len(expected):
        raise RuntimeConfigError("runtime引用提取数量与原始定义不符")
    for stored, raw in zip(actual, expected, strict=True):
        candidates = index.get((raw["target_kind"], raw["target_key_json"]), []) if index is not None else [dict(r) for r in conn.execute("SELECT definition_id,owner_definition_id FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?", (line, raw["target_kind"], raw["target_key_json"]))]
        status, target, identifiers = resolve_reference(raw, candidates)
        raw.update(resolution_status=status, resolved_definition_id=target, candidate_ids_json=dumps(identifiers))
        if any(stored[key] != value for key, value in raw.items()):
            raise RuntimeConfigError("runtime引用结构、精确目标或作用域与原始定义不符")
    return actual


@database_write
def build_runtime_layer(
    db_path: Path,
    repo_dir: Path,
    *,
    version_line: str,
    upstream_commit: str,
    manifest: dict | None = None,
    content_timestamp: str | None = None,
):
    validate_line(version_line)
    if not isinstance(upstream_commit, str) or not upstream_commit:
        raise ValueError("runtime构建需要明确upstream_commit")
    actual = enumerate_runtime_sources(repo_dir)
    if manifest is not None and actual != manifest:
        raise RuntimeConfigError("runtime来源清单在构建前发生变化", "source_changed", 500)
    manifest = actual
    normalization = for_commit(upstream_commit)
    snapshot = _normalized_snapshot(version_line, upstream_commit, manifest, normalization)
    built_at = content_timestamp or datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            ensure_schema(conn)
            for table in TABLE_COLUMNS:
                conn.execute(f"DELETE FROM {table} WHERE version_line=?", (version_line,))
            conn.execute("DELETE FROM data_component WHERE component=? AND version_line=?", (COMPONENT, version_line))
            conn.execute("DELETE FROM source_manifest WHERE component=? AND version_line=?", (COMPONENT, version_line))
            all_types = collections.Counter()
            family_counts = collections.Counter()
            for order, (relative, spec) in enumerate(manifest["files"].items()):
                payload = (Path(repo_dir) / relative).read_bytes()
                if len(payload) != spec["size_bytes"] or hashlib.sha256(payload).hexdigest() != spec["sha256"]:
                    raise RuntimeConfigError(f"runtime来源构建期间改变: {relative}", "source_changed", 500)
                data = parse_source(payload)
                definitions = extract_definitions(relative, spec["family"], data, snapshot,
                                                  normalization=normalization, indexer_version=INDEXER_VERSION)
                common = {"version_line": version_line, "snapshot_id": snapshot}
                _insert(conn, "runtime_source", {"source_path": relative, "source_family": spec["family"],
                    "upstream_commit": upstream_commit, "source_sha256": spec["sha256"], "source_size_bytes": len(payload),
                    "payload_codec": "zlib", "payload_blob": zlib.compress(payload), "json_root_type": type(data).__name__,
                    "indexed_definition_count": len(definitions), "source_order": order, "imported_at": built_at, **common})
                for definition in definitions:
                    _insert(conn, "runtime_definition", {k: v for k, v in {**definition, **common}.items() if k != "payload"})
                    for reference in extract_references(definition, normalization=normalization,
                                                        indexer_version=INDEXER_VERSION):
                        _insert(conn, "runtime_reference", {**reference, **common})
                    for reference in extract_object_references(definition, normalization=normalization,
                                                               indexer_version=INDEXER_VERSION):
                        _insert(conn, "runtime_object_reference", {**reference, **common})
                    for reference in extract_material_references(definition, normalization=normalization,
                                                                 indexer_version=INDEXER_VERSION):
                        _insert(conn, "runtime_material_reference", {**reference, **common})
                    for binding in extract_parameter_bindings(definition, normalization=normalization):
                        _insert(conn, "runtime_parameter_binding", {**binding, **common})
                all_types.update(n["$type"] for _, n in walk(data) if isinstance(n, dict) and isinstance(n.get("$type"), str))
                family_counts[spec["family"]] += 1
                conn.execute("INSERT INTO source_manifest VALUES (?,?,?,?,?,?,?)",
                             (COMPONENT, version_line, relative, upstream_commit, spec["sha256"], len(definitions), built_at))
            index = collections.defaultdict(list)
            for row in conn.execute("SELECT definition_id,definition_kind,exact_key_json,owner_definition_id FROM runtime_definition WHERE version_line=?", (version_line,)):
                index[(row["definition_kind"], row["exact_key_json"])].append(dict(row))
            updates = []
            for row in conn.execute("SELECT * FROM runtime_reference WHERE version_line=? AND resolution_status='unresolved'", (version_line,)):
                candidates = index.get((row["target_kind"], row["target_key_json"]), [])
                state, target, identifiers = resolve_reference(row, candidates)
                updates.append((state, target, dumps(identifiers), version_line, row["reference_id"]))
            conn.executemany("UPDATE runtime_reference SET resolution_status=?,resolved_definition_id=?,candidate_ids_json=? WHERE version_line=? AND reference_id=?", updates)
            if INDEXER_VERSION in ("5", "6", "7"):
                updates = []
                for row in conn.execute("SELECT * FROM runtime_object_reference WHERE version_line=? AND resolution_status='unresolved'", (version_line,)):
                    state, target, identifiers = resolve_object_reference(row, index.get((row["target_kind"], row["target_key_json"]), []))
                    updates.append((state, target, dumps(identifiers), version_line, row["reference_id"]))
                conn.executemany("UPDATE runtime_object_reference SET resolution_status=?,resolved_definition_id=?,candidate_ids_json=? WHERE version_line=? AND reference_id=?", updates)
            if INDEXER_VERSION in ("6", "7"):
                updates = []
                for row in conn.execute("SELECT * FROM runtime_material_reference WHERE version_line=? AND resolution_status='unresolved'", (version_line,)):
                    state, target, identifiers = resolve_material_reference(row, index.get((row["target_kind"], row["target_key_json"]), []))
                    updates.append((state, target, dumps(identifiers), version_line, row["reference_id"]))
                conn.executemany("UPDATE runtime_material_reference SET resolution_status=?,resolved_definition_id=?,candidate_ids_json=? WHERE version_line=? AND reference_id=?", updates)
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE version_line=?", (version_line,)).fetchone()[0] for t in tables_for_indexer(INDEXER_VERSION)}
            states = dict(conn.execute("SELECT resolution_status,COUNT(*) FROM runtime_reference WHERE version_line=? GROUP BY resolution_status", (version_line,)))
            report = {"schema_version": SCHEMA_VERSION, "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
                      "indexer_version": INDEXER_VERSION, "snapshot_id": snapshot, "manifest_fingerprint": manifest["fingerprint"],
                      "total_source_bytes": manifest["total_bytes"], "compressed_bytes": conn.execute("SELECT SUM(length(payload_blob)) FROM runtime_source WHERE version_line=?", (version_line,)).fetchone()[0],
                      "family_counts": dict(family_counts), "table_counts": counts, "reference_states": states,
                      "types": dict(sorted(all_types.items())), "types_representation": "raw",
                      "execution_support": "not_evaluated",
                      # A structurally valid raw catalog is still queryable
                      # without a commit-bound normalization profile, but
                      # callers must not mistake that for reviewed canonical
                      # field/type interpretation.
                      "normalization_state": (
                          "ready" if normalization is not None else "unavailable"
                      )}
            report.update(
                source_capability_contract=manifest["capability_contract"],
                source_capability_state=manifest["capability_state"],
                source_capabilities=manifest["capabilities"],
            )
            if normalization is not None:
                report["normalization"] = normalization.receipt(upstream_commit)
            if INDEXER_VERSION in ("5", "6", "7"):
                report["object_reference_states"] = dict(conn.execute("SELECT resolution_status,COUNT(*) FROM runtime_object_reference WHERE version_line=? GROUP BY resolution_status", (version_line,)))
            if INDEXER_VERSION in ("6", "7"):
                report["material_reference_states"] = dict(conn.execute("SELECT resolution_status,COUNT(*) FROM runtime_material_reference WHERE version_line=? GROUP BY resolution_status", (version_line,)))
            conn.execute("INSERT INTO data_component VALUES (?,?,?,?,?,?)", (COMPONENT, version_line, SCHEMA_VERSION, upstream_commit, built_at, dumps(report)))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('runtime_schema_version',?)", (str(SCHEMA_VERSION),))
            validate_runtime(conn, version_line, deep=True)
            if enumerate_runtime_sources(repo_dir) != manifest:
                raise RuntimeConfigError("runtime来源清单在构建期间发生变化", "source_changed", 500)
        return report
    finally:
        conn.close()


def runtime_datasets(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {"runtime_config": {"supported_schema_versions": [SCHEMA_VERSION], "profile_id": PROFILE_ID,
                                "profile_version": PROFILE_VERSION, "indexer_version": INDEXER_VERSION,
                                "supported_indexer_versions": list(SUPPORTED_INDEXER_VERSIONS), "lines": {}}}
    marker = conn.execute("SELECT value FROM meta WHERE key='runtime_schema_version'").fetchone() if "meta" in tables else None
    for line in ("release", "beta"):
        state = {"state": "missing", "index_state": "missing", "schema_version": None,
                 "upstream_commit": None, "snapshot_id": None, "execution_support": "not_evaluated"}
        try:
            if marker and marker[0] != str(SCHEMA_VERSION):
                state.update(state="incompatible", schema_version=marker[0])
            elif not REQUIRED_TABLES <= tables:
                if marker:
                    state["state"] = "invalid"
            else:
                row = conn.execute("SELECT schema_version,upstream_commit,built_at,validation_json FROM data_component WHERE component=? AND version_line=?", (COMPONENT, line)).fetchone()
                if row:
                    summary = json.loads(row[3])
                    if not isinstance(summary, dict):
                        raise RuntimeConfigError("运行配置组件回执必须为对象")
                    state.update(schema_version=row[0], upstream_commit=row[1], built_at=row[2],
                                 snapshot_id=summary.get("snapshot_id"), profile_id=summary.get("profile_id"),
                                 profile_version=summary.get("profile_version"), indexer_version=summary.get("indexer_version"),
                                 manifest_fingerprint=summary.get("manifest_fingerprint"), counts=summary.get("table_counts"),
                                 reference_states=summary.get("reference_states"),
                                  normalization_state=summary.get(
                                      "normalization_state",
                                      "ready" if "normalization" in summary else "unavailable",
                                  ),
                                 source_capability_contract=summary.get("source_capability_contract"),
                                 source_capability_state=summary.get("source_capability_state"),
                                 source_capabilities=summary.get("source_capabilities"))
                    if row[0] != SCHEMA_VERSION or (summary.get("profile_id"), summary.get("profile_version")) != (PROFILE_ID, PROFILE_VERSION) or summary.get("indexer_version") not in SUPPORTED_INDEXER_VERSIONS:
                        state["state"] = "incompatible"
                    else:
                        _normalization_for_summary(summary, row[1])
                        if "normalization" in summary:
                            state["normalization"] = summary["normalization"]
                        needed = tables_for_indexer(summary["indexer_version"])
                        valid = marker is not None and set(needed) <= tables and all(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE version_line=?", (line,)).fetchone()[0] == summary.get("table_counts", {}).get(t) for t in needed)
                        valid = valid and conn.execute("SELECT COUNT(*) FROM source_manifest WHERE component=? AND version_line=?", (COMPONENT, line)).fetchone()[0] == summary.get("table_counts", {}).get("runtime_source")
                        state["state"] = "ready" if valid and row[1] and state["snapshot_id"] else "invalid"
        except (sqlite3.DatabaseError, RuntimeConfigError, ValueError, TypeError, KeyError):
            state["state"] = "invalid"
        state["index_state"] = state["state"]
        result["runtime_config"]["lines"][line] = state
    return result


def validate_runtime(conn, version_line, *, deep=False):
    validate_line(version_line)
    prior_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        state = runtime_datasets(conn)[COMPONENT]["lines"][version_line]
        if state["state"] != "ready":
            raise RuntimeConfigError(f"运行配置索引状态: {state['state']}")
        normalization = _normalization_for_summary(state, state["upstream_commit"])
        snapshot = state["snapshot_id"]
        for table in tables_for_indexer(state["indexer_version"]):
            if conn.execute(f"SELECT 1 FROM {table} WHERE version_line=? AND snapshot_id!=? LIMIT 1", (version_line, snapshot)).fetchone():
                raise RuntimeConfigError("运行配置存在跨快照行", "mixed_snapshot", 500)
        columns = "*" if deep else "source_path,source_family,upstream_commit,source_sha256,source_size_bytes,indexed_definition_count"
        rows = conn.execute(f"SELECT {columns} FROM runtime_source WHERE version_line=? ORDER BY source_path", (version_line,)).fetchall()
        files = {r["source_path"]: {"family": r["source_family"], "sha256": r["source_sha256"], "size_bytes": r["source_size_bytes"]} for r in rows}
        capabilities = state.get("source_capabilities")
        capability_contract = state.get("source_capability_contract")
        if capabilities is None:
            # Legacy indexer-7 receipts predate capability-aware imports and
            # promised GlobalCombat unconditionally.
            if state["indexer_version"] == "7" and files.get(GLOBAL_COMBAT_PATH, {}).get("family") != "global_combat":
                raise RuntimeConfigError("runtime索引7缺少同快照公共战斗原配置来源")
        else:
            if capability_contract != SOURCE_CAPABILITY_CONTRACT or not isinstance(capabilities, dict):
                raise RuntimeConfigError("runtime来源能力回执无效")
            known_states = {"ready", "unavailable"}
            known_requirements = {"required", "optional"}
            family_counts = collections.Counter(spec["family"] for spec in files.values())
            for family, receipt in capabilities.items():
                if (not isinstance(family, str) or not isinstance(receipt, dict)
                        or set(receipt) != {"state", "requirement"}
                        or receipt["state"] not in known_states
                        or receipt["requirement"] not in known_requirements):
                    raise RuntimeConfigError("runtime来源能力回执无效")
                if receipt["requirement"] == "required" and receipt["state"] != "ready":
                    raise RuntimeConfigError("runtime必需来源能力不可用")
                present = family_counts.get(family, 0) > 0
                # Optional families have disjoint source ownership, so their
                # availability can be proven from the stored rows.  Some core
                # exact families intentionally overlap the broad ``excel``
                # catalog and are instead covered by the manifest hash.
                if (receipt["requirement"] == "optional"
                        and (receipt["state"] == "ready") != present):
                    raise RuntimeConfigError("runtime来源能力状态与同快照文件不一致")
            expected_capability_state = (
                "ready"
                if all(row["state"] == "ready" for row in capabilities.values())
                else "degraded"
            )
            if state.get("source_capability_state") != expected_capability_state:
                raise RuntimeConfigError("runtime来源能力汇总状态不一致")
        if state["indexer_version"] in ("5", "6", "7") and files.get(GADGET_EXCEL_PATH, {}).get("family") != "gadget_excel":
            raise RuntimeConfigError("runtime索引5缺少同快照GadgetExcel来源")
        if state["indexer_version"] in ("6", "7") and files.get(MATERIAL_EXCEL_PATH, {}).get("family") != "material_excel":
            raise RuntimeConfigError("runtime索引6缺少同快照MaterialExcel来源")
        manifest_body = {"profile_id": state["profile_id"], "profile_version": state["profile_version"], "indexer_version": state["indexer_version"], "files": files, "total_bytes": sum(r["source_size_bytes"] for r in rows)}
        if capabilities is not None:
            manifest_body.update(
                capability_contract=capability_contract,
                capability_state=state.get("source_capability_state"),
                capabilities=capabilities,
            )
        if fingerprint(manifest_body) != state["manifest_fingerprint"]:
            raise RuntimeConfigError("runtime manifest指纹错误")
        if normalization is not None:
            expected_snapshot = _normalized_snapshot(version_line, state["upstream_commit"],
                                                     {**manifest_body, "fingerprint": state["manifest_fingerprint"]}, normalization)
            if expected_snapshot != snapshot:
                raise RuntimeConfigError("runtime snapshot与归一化档案不符", "mixed_snapshot", 500)
        manifests = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT source_file,upstream_commit,sha256 FROM source_manifest WHERE component=? AND version_line=?", (COMPONENT, version_line))}
        if manifests != {r["source_path"]: (state["upstream_commit"], r["source_sha256"]) for r in rows} or any(r["upstream_commit"] != state["upstream_commit"] for r in rows):
            raise RuntimeConfigError("runtime来源manifest或commit错误", "mixed_snapshot", 500)
        if conn.execute("SELECT 1 FROM runtime_definition d LEFT JOIN runtime_source s ON d.version_line=s.version_line AND d.source_path=s.source_path WHERE d.version_line=? AND s.source_path IS NULL LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime定义缺来源")
        if conn.execute("SELECT 1 FROM runtime_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.resolved_definition_id=d.definition_id WHERE r.version_line=? AND r.resolution_status='resolved' AND d.definition_id IS NULL LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime已解析引用断链")
        if conn.execute("SELECT 1 FROM runtime_reference r JOIN runtime_definition d ON r.version_line=d.version_line AND r.resolved_definition_id=d.definition_id WHERE r.version_line=? AND r.resolution_status='resolved' AND (d.definition_kind!=r.target_kind OR d.exact_key_json!=r.target_key_json) LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime已解析引用的类型或精确键错误")
        if conn.execute("SELECT 1 FROM runtime_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.from_definition_id=d.definition_id WHERE r.version_line=? AND d.definition_id IS NULL LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime引用来源定义断链")
        allowed_owner = "(d.definition_kind='modifier' AND p.definition_kind='ability')"
        if state["indexer_version"] in ("5", "6", "7"):
            allowed_owner += " OR (d.definition_kind='gadget_config_entry' AND p.definition_kind='gadget_config')"
        if conn.execute(f"SELECT 1 FROM runtime_definition d LEFT JOIN runtime_definition p ON d.version_line=p.version_line AND d.owner_definition_id=p.definition_id WHERE d.version_line=? AND d.owner_definition_id IS NOT NULL AND (p.definition_id IS NULL OR NOT ({allowed_owner}) OR p.source_path!=d.source_path) LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime局部定义归属断链")
        if state["indexer_version"] in ("5", "6", "7"):
            if conn.execute("SELECT 1 FROM runtime_object_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.from_definition_id=d.definition_id WHERE r.version_line=? AND d.definition_id IS NULL LIMIT 1", (version_line,)).fetchone():
                raise RuntimeConfigError("runtime对象引用来源定义断链")
            if conn.execute("SELECT 1 FROM runtime_object_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.resolved_definition_id=d.definition_id WHERE r.version_line=? AND r.resolution_status='resolved' AND (d.definition_id IS NULL OR d.definition_kind!=r.target_kind OR d.exact_key_json!=r.target_key_json) LIMIT 1", (version_line,)).fetchone():
                raise RuntimeConfigError("runtime对象引用精确目标断链")
            if conn.execute("SELECT 1 FROM runtime_object_reference o LEFT JOIN runtime_reference r ON o.version_line=r.version_line AND o.legacy_reference_id=r.reference_id WHERE o.version_line=? AND o.legacy_reference_id IS NOT NULL AND (r.reference_id IS NULL OR r.from_definition_id!=o.from_definition_id OR r.json_pointer!=o.json_pointer OR r.target_key_json!=o.target_key_json OR r.relation_kind!='external_asset') LIMIT 1", (version_line,)).fetchone():
                raise RuntimeConfigError("runtime对象引用与旧创建引用不一致")
        if state["indexer_version"] in ("6", "7"):
            if conn.execute("SELECT 1 FROM runtime_material_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.from_definition_id=d.definition_id WHERE r.version_line=? AND (d.definition_id IS NULL OR d.definition_kind NOT IN ('ability','modifier') OR d.source_path!=r.source_path) LIMIT 1", (version_line,)).fetchone():
                raise RuntimeConfigError("runtime物品引用来源定义断链")
            if conn.execute("SELECT 1 FROM runtime_material_reference r LEFT JOIN runtime_definition d ON r.version_line=d.version_line AND r.resolved_definition_id=d.definition_id WHERE r.version_line=? AND r.resolution_status='resolved' AND (r.target_kind!='material_record' OR d.definition_id IS NULL OR d.definition_kind!=r.target_kind OR d.exact_key_json!=r.target_key_json OR d.source_path!=?) LIMIT 1", (version_line, MATERIAL_EXCEL_PATH)).fetchone():
                raise RuntimeConfigError("runtime物品引用精确目标断链")
            if conn.execute("SELECT 1 FROM runtime_material_reference m LEFT JOIN runtime_reference r ON m.version_line=r.version_line AND m.legacy_reference_id=r.reference_id WHERE m.version_line=? AND m.legacy_reference_id IS NOT NULL AND (r.reference_id IS NULL OR r.from_definition_id!=m.from_definition_id OR r.json_pointer!=m.json_pointer OR r.source_path!=m.source_path OR r.target_key_json!=m.target_key_json OR r.field_name!='configID' OR r.relation_kind!='external_asset') LIMIT 1", (version_line,)).fetchone():
                raise RuntimeConfigError("runtime物品引用与旧产球引用不一致")
        if conn.execute("SELECT 1 FROM runtime_parameter_binding b LEFT JOIN runtime_definition d ON b.version_line=d.version_line AND b.origin_definition_id=d.definition_id WHERE b.version_line=? AND (d.definition_id IS NULL OR d.definition_kind!='talent_config') LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime参数绑定来源断链")
        if conn.execute("SELECT 1 FROM runtime_parameter_binding b LEFT JOIN runtime_reference r ON b.version_line=r.version_line AND b.target_reference_id=r.reference_id WHERE b.version_line=? AND b.target_reference_id IS NOT NULL AND (r.reference_id IS NULL OR r.from_definition_id!=b.origin_definition_id OR r.target_kind!='ability') LIMIT 1", (version_line,)).fetchone():
            raise RuntimeConfigError("runtime参数绑定目标断链")
        if deep:
            index = collections.defaultdict(list)
            for definition in conn.execute("SELECT definition_id,definition_kind,exact_key_json,owner_definition_id FROM runtime_definition WHERE version_line=?", (version_line,)):
                index[(definition["definition_kind"], definition["exact_key_json"])].append(dict(definition))
            for row in rows:
                data = decode_source(row)
                definitions = conn.execute("SELECT * FROM runtime_definition WHERE version_line=? AND source_path=?", (version_line, row["source_path"])).fetchall()
                if len(definitions) != row["indexed_definition_count"]:
                    raise RuntimeConfigError("runtime源定义计数不一致")
                raw_definitions = {d["definition_id"]: d for d in extract_definitions(
                    row["source_path"], row["source_family"], data, snapshot,
                    normalization=normalization, indexer_version=state["indexer_version"])}
                if {d["definition_id"] for d in definitions} != raw_definitions.keys():
                    raise RuntimeConfigError("runtime定义索引与原始来源不符")
                for definition in definitions:
                    try:
                        value = resolve_pointer(data, definition["json_pointer"])
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise RuntimeConfigError("runtime定义pointer无效") from exc
                    if fingerprint(value) != definition["node_sha256"]:
                        raise RuntimeConfigError("runtime定义节点指纹错误")
                    if any(definition[k] != v for k, v in raw_definitions[definition["definition_id"]].items() if k != "payload"):
                        raise RuntimeConfigError("runtime定义身份与原始来源不符")
                    validate_definition_references(conn, version_line, definition, value, index=index,
                                                   normalization=normalization, indexer_version=state["indexer_version"])
                    if state["indexer_version"] in ("5", "6", "7"):
                        validate_definition_object_references(conn, version_line, definition, value, index=index,
                                                              normalization=normalization, indexer_version=state["indexer_version"])
                    if state["indexer_version"] in ("6", "7"):
                        validate_definition_material_references(conn, version_line, definition, value, index=index,
                                                                normalization=normalization, indexer_version=state["indexer_version"])
                    if definition["definition_kind"] == "talent_config":
                        bindings = conn.execute("SELECT * FROM runtime_parameter_binding WHERE version_line=? AND origin_definition_id=? ORDER BY operation_index", (version_line, definition["definition_id"])).fetchall()
                        expected_bindings = extract_parameter_bindings({**dict(definition), "payload": value}, normalization=normalization)
                        if len(bindings) != len(expected_bindings) or any(any(stored[k] != v for k, v in expected.items()) for stored, expected in zip(bindings, expected_bindings, strict=True)):
                            raise RuntimeConfigError("runtime参数绑定索引与原始操作不符")
        return state
    finally:
        conn.row_factory = prior_factory


def runtime_complete(conn, *, version_line, upstream_commit, manifest):
    state = runtime_datasets(conn)[COMPONENT]["lines"][version_line]
    if state["state"] != "ready" or state["upstream_commit"] != upstream_commit:
        return False
    if (state.get("profile_id"), state.get("profile_version"), state.get("indexer_version"), state.get("manifest_fingerprint")) != (PROFILE_ID, PROFILE_VERSION, INDEXER_VERSION, manifest["fingerprint"]):
        return False
    try:
        # The unchanged decision certifies stored bodies/indexes, not just a previous marker.
        validate_runtime(conn, version_line, deep=True)
    except (RuntimeConfigError, sqlite3.DatabaseError):
        return False
    return True
