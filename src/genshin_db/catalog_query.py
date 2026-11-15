"""Bounded same-snapshot identity queries shared by CLI and embedded agents."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from genshin_db import queries
from genshin_db.context_query import fingerprint
from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.publication_evidence import publication_by_id
from genshin_db.validation import version_line as validate_line

LIMITS = {'page': 10000, 'page_size': 30, 'query_chars': 128, 'response_bytes': 131072}
FIELDS = {'kind', 'query', 'page', 'page_size', 'version_line'}


def catalog_capabilities():
    return {'contract': 'public-catalog-query-v1', 'kinds': ['character', 'weapon', 'artifact_set'],
            'limits': copy.deepcopy(LIMITS), 'required': ['kind', 'query'],
            'optional': ['page', 'page_size', 'version_line'], 'database_read_only': True,
            'directory_access': '复用数据库跨进程锁，数据目录需要允许打开锁文件；不启动服务、不建库。',
            'matching': '复用公共名称查询的LIKE匹配；可能多条，同名按原始ID保留，不自动选择。',
            'scope': '公共身份定位；publication仅说明实体上线证据，实际采用需确认时按任务询问。正文、参数、机制和完整证据可按ID用context/runtime另查。'}


def validate_catalog_request(request):
    if not isinstance(request, dict) or request.keys() - FIELDS or not {'kind', 'query'} <= request.keys():
        raise ValueError('目录请求只允许kind/query/page/page_size/version_line，kind与query必填')
    kind = request['kind']
    if not isinstance(kind, str) or kind not in ('character', 'weapon', 'artifact_set'):
        raise ValueError('目录kind必须为character/weapon/artifact_set')
    query = request['query']
    if not isinstance(query, str) or not query.strip() or not 1 <= len(query) <= LIMITS['query_chars']:
        raise ValueError('检索词必须为1–128字符的非空文本')
    result = {'kind': kind, 'query': query.strip(), 'version_line': request.get('version_line', 'release')}
    validate_line(result['version_line'])
    for key, default in [('page', 1), ('page_size', 20)]:
        value = request.get(key, default)
        if type(value) is not int or not 1 <= value <= LIMITS[key]:
            raise ValueError(f'{key}必须为1–{LIMITS[key]}的整数')
        result[key] = value
    return result


def query_catalog(db_path, request):
    request = validate_catalog_request(request)
    path = Path(db_path)
    if not path.is_file():
        # The shared read-session takes a filesystem lock; reject before it can
        # create any parent directory/lock for a nonexistent database.
        raise FileNotFoundError('数据库不存在；资料查询不会创建或更新数据库')
    function = {'character': queries.list_characters, 'weapon': queries.list_weapons,
                'artifact_set': queries.list_artifact_sets}[request['kind']]
    with equipment_read_session(path):
        meta = queries.get_meta(path)
        if request['version_line'] not in meta.get('version_lines', []):
            raise ValueError('数据库没有所选版本线；不会回退到其它版本')
        rows = function(path, request['version_line'], name=request['query'],
                        page=request['page'], page_size=request['page_size'])
        result = {**request, 'total': rows['total'],
                  'items': [{key: item[key] for key in ('id', 'name', 'element', 'rarity', 'weapon_type') if key in item}
                            for item in rows['items']],
                  'snapshot': {**meta, 'version_line': request['version_line']}}
        states = publication_by_id(borrow_read_connection(path), request['kind'],
                                   [row['id'] for row in result['items']], request['version_line'])
        for row in result['items']:
            row['publication'] = states[row['id']]
        result['snapshot']['fingerprint'] = fingerprint({'request': request, 'meta': meta, 'items': result['items'], 'total': result['total']})
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(encoded) > LIMITS['response_bytes']:
        raise ValueError('目录结果超过有界响应大小，未裁剪身份或元信息')
    return result
