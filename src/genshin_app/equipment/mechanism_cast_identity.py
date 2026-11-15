"""Bounded full-node cast identity, shared fresh/offline traversal, no gameplay VM."""
from __future__ import annotations

import copy
import re

from genshin_db.runtime_action_resource import (
    _resolve_action_identity,
    resolve_action_resource_entry,
)
from genshin_runtime.mechanism_semantics import finite_json

from .files import fingerprint
from .mechanism_execution_scope import need
from .mechanism_parent_graph import _snapshot_equal

IDENTITY_VERSION = 1
MAX_IDENTITY_BYTES = 256 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")


def query_cast_identity(db_path, query, snapshot):
    """Internal only: exact same snapshot, complete nodes, no native prepared proof."""
    from genshin_db.runtime_queries import _read

    with _read(db_path, snapshot['version_line'], snapshot['snapshot_id']) as (conn, state):
        need(_snapshot_equal(state, snapshot), '施放身份与机制资料不属于同一快照')
        entry, nodes = resolve_action_resource_entry(conn, state, **query)
        records = {}
        for key, row in entry['identity_sources'].items():
            raw, origin = nodes.node(row)
            records[key] = {**origin, 'node_sha256': row['node_sha256'], 'raw': copy.deepcopy(raw)}
        proof = {'schema_version': IDENTITY_VERSION, 'snapshot': copy.deepcopy(state),
                 'query': copy.deepcopy(query), 'selection': copy.deepcopy(entry['selection']), 'nodes': records}
        finite_json(proof, MAX_IDENTITY_BYTES)
        return proof


def verify_cast_identity(proof, query, snapshot):
    """Rebuild Avatar -> depot -> skill from saved full records, without a DB."""
    finite_json(proof, MAX_IDENTITY_BYTES)
    need(type(proof) is dict and set(proof) == {'schema_version', 'snapshot', 'query', 'selection', 'nodes'}
         and type(proof['schema_version']) is int and proof['schema_version'] == IDENTITY_VERSION,
         '施放身份证明格式不受支持')
    need(proof['query'] == query and _snapshot_equal(proof['snapshot'], snapshot), '施放身份选择或快照不一致')
    records = proof['nodes']
    need(type(records) is dict and set(records) == {'avatar', 'depot', 'skill'}, '施放身份完整节点缺失')

    def exact(table, key, name, fields):
        row = records[name]
        need(type(row) is dict and set(row) == {'source_path', 'source_sha256', 'json_pointer', 'node_sha256', 'raw'},
             '施放身份节点字段不完整')
        need(row['source_path'] == f'ExcelBinOutput/{table}ExcelConfigData.json'
             and isinstance(row['json_pointer'], str) and re.fullmatch(r'/(0|[1-9][0-9]*)', row['json_pointer']),
             '施放身份节点来源或位置错误')
        need(all(isinstance(row[k], str) and _SHA.fullmatch(row[k]) for k in ('source_sha256', 'node_sha256')),
             '施放身份节点指纹格式错误')
        raw = row['raw']
        need(type(raw) is dict and fingerprint(raw) == row['node_sha256']
             and all(type(raw.get(k)) is type(v) and raw.get(k) == v for k, v in key.items()),
             '施放身份完整节点与选择不符')
        return copy.deepcopy(raw)

    result = _resolve_action_identity(exact, **query)
    need(result['selection'] == proof['selection'], '施放身份无法从完整节点重建')
    return copy.deepcopy(result['selection'])


def identity_provider(db_path):
    """Per-compose cache; retained evidence is bounded by the group receipt budget."""
    cache = {}

    def query(request, snapshot):
        key = fingerprint({'query': request, 'snapshot': snapshot})
        if key not in cache:
            need(len(cache) < 64, '施放身份查询超过限额')
            cache[key] = query_cast_identity(db_path, request, snapshot)
        return copy.deepcopy(cache[key])

    return query
