"""Bounded, strict inventory of every current semantic fact head.

This module deliberately stops at the storage envelope.  Consumers classify
the validated records for their own ownership domain, but cannot discard an
unrelated head before its complete record and stored fingerprints are checked.
"""

from __future__ import annotations

import re

from genshin_db.semantic_store import HEAD_TABLE, SCHEMA_VERSION, TABLES

MAX_CLASSIFICATION_FACTS = 4096
MAX_CLASSIFICATION_BYTES = 16 * 1024 * 1024
MAX_CLASSIFICATION_RECORD_BYTES = 2 * 1024 * 1024

KNOWN_FACT_KINDS = (
    "action_admission_model",
    "action_capacity_delta",
    "action_capacity_model",
    "attribute_output",
    "attribute_read",
    "attribute_sample",
    "execution_qualification",
    "execution_requirement",
    "parameter_role",
    "related_action_root",
    "rule_template",
)


def read_current_fact_inventory(
    conn,
    version_line,
    *,
    columns,
    decode,
    require,
    current_code,
    budget_code,
    max_facts,
    max_record_bytes,
    max_total_bytes,
    known_fact_kinds=KNOWN_FACT_KINDS,
):
    """Return every validated current fact in stable head order.

    ``require`` and ``decode`` are supplied by the owning runtime so its public
    exception type and test seams remain stable.  Stored bytes are charged
    before fetching and decoding the body.  The returned ``envelope`` is the
    canonical minimum material consumers must retain in inventory fingerprints.
    """
    byte_size = "+".join(
        f"coalesce(length(CAST({column} AS BLOB)),0)" for column in columns
    )
    storage_types = " AND ".join(
        f"typeof({column}) IN ('text','null')"
        if column in ("reviewer", "reviewed_at")
        else f"typeof({column})='integer'"
        if column in ("revision", "schema_version")
        else f"typeof({column})='text'"
        for column in columns
    )
    heads = conn.execute(
        "SELECT CASE WHEN typeof(record_kind)='text' "
        "AND record_kind IN ('fact','descriptor') THEN record_kind END, "
        "CASE WHEN typeof(record_id)='text' "
        "AND length(CAST(record_id AS BLOB)) BETWEEN 1 AND 80 THEN record_id END, "
        "CASE WHEN typeof(revision)='integer' AND revision BETWEEN 1 AND 2147483647 "
        "THEN revision END, CASE WHEN typeof(updated_at)='text' "
        "AND length(updated_at) BETWEEN 1 AND 80 "
        "AND length(CAST(updated_at AS BLOB))<=320 THEN updated_at END "
        f"FROM {HEAD_TABLE} WHERE version_line=? "
        "ORDER BY record_kind,record_id,revision,updated_at LIMIT ?",
        (version_line, max_facts + 1),
    )
    seen = set()
    inventory = []
    used_bytes = 0
    try:
        for index, head in enumerate(heads):
            require(
                index < max_facts,
                "当前语义头完整分类超过数量预算，不能截断",
                budget_code,
            )
            record_kind, ident, revision, updated_at = head
            require(
                record_kind in ("fact", "descriptor")
                and type(ident) is str
                and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", ident)
                and type(revision) is int
                and updated_at is not None,
                "当前语义头种类、身份或元数据无效",
                current_code,
            )
            head_key = (record_kind, ident)
            require(head_key not in seen, "当前语义头身份重复", current_code)
            seen.add(head_key)
            if record_kind == "descriptor":
                continue

            where = " WHERE version_line=? AND record_id=? AND revision=?"
            params = (version_line, ident, revision)
            storage = conn.execute(
                f"SELECT ({storage_types}), ({byte_size}), "
                f"CASE WHEN typeof(schema_version)='integer' THEN schema_version END "
                f"FROM {TABLES['fact']}" + where + " LIMIT 2",
                params,
            ).fetchall()
            require(len(storage) == 1, "当前事实修订缺失或身份重复", current_code)
            valid_types, row_bytes, schema = storage[0]
            require(
                valid_types == 1 and type(schema) is int and schema == SCHEMA_VERSION,
                "当前事实记录缺失或存储类型无效",
                current_code,
            )
            require(
                type(row_bytes) is int and row_bytes <= max_record_bytes,
                "当前事实单条存储字节超过完整分类预算",
                budget_code,
            )
            require(
                used_bytes + row_bytes <= max_total_bytes,
                "当前事实累计存储字节超过完整分类预算",
                budget_code,
            )
            used_bytes += row_bytes
            row = conn.execute(
                f"SELECT * FROM {TABLES['fact']}" + where + " LIMIT 1", params
            ).fetchone()
            try:
                record = decode(row, "fact")
            except (
                ValueError,
                TypeError,
                KeyError,
                RecursionError,
                OverflowError,
            ) as exc:
                try:
                    require(False, "当前事实完整封套或指纹无效，不能排除", current_code)
                except Exception as wrapped:
                    raise wrapped from exc
                raise AssertionError("require must raise") from exc  # pragma: no cover
            require(
                record["id"] == ident
                and record["revision"] == revision
                and record["storage"]["version_line"] == version_line,
                "当前事实头与完整记录不一致",
                current_code,
            )
            fact_kind = record["payload"]["kind"]
            require(
                fact_kind in known_fact_kinds,
                "当前事实合同无法严格分类",
                current_code,
            )
            inventory.append(
                {
                    "envelope": {
                        "record_kind": "fact",
                        "id": ident,
                        "revision": revision,
                        "head_updated_at": updated_at,
                        "payload_fingerprint": record["payload_fingerprint"],
                        "review_fingerprint": record["review_fingerprint"],
                        "fact_kind": fact_kind,
                        "review_status": record["review"]["status"],
                    },
                    "record": record,
                }
            )
    finally:
        heads.close()
    return inventory


__all__ = [
    "KNOWN_FACT_KINDS",
    "MAX_CLASSIFICATION_BYTES",
    "MAX_CLASSIFICATION_FACTS",
    "MAX_CLASSIFICATION_RECORD_BYTES",
    "read_current_fact_inventory",
]
