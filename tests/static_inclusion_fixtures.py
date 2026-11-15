"""Small actual-material fixture; database admission always re-reads this snapshot.

Queries are isolated from production data. The real SQLite parent identity index
and real read-session machinery remain active; fixed raw/normalized query replies
come from the same archived database snapshot and are freshly deep-copied.
"""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_db import equipment_queries, queries, runtime_queries
from genshin_db.runtime_catalog import dumps

FIXTURES = Path(__file__).parent / 'fixtures'
MATERIALS = json.loads((FIXTURES / 'static_inclusion_materials.json').read_text(encoding='utf8'))
EQUIPMENT = json.loads((FIXTURES / 'static_inclusion_equipment.json').read_text(encoding='utf8'))


class _DatabasePath:
    path = None

    def __fspath__(self):
        assert self.path is not None
        return str(self.path)


DB = _DatabasePath()


@pytest.fixture(scope='module', autouse=True)
def snapshot_session(tmp_path_factory):
    DB.path = tmp_path_factory.mktemp('static-source-db') / 'isolated.db'
    parents = {m['parent']['definition']['definition_id']: m['parent'] for m in MATERIALS}
    sources = {m['source']['source']['affix_id']: m['source'] for m in MATERIALS}
    with sqlite3.connect(DB.path) as conn:
        conn.execute('CREATE TABLE runtime_definition (definition_id TEXT, version_line TEXT, source_path TEXT, exact_key_json TEXT)')
        for ident, parent in parents.items():
            raw = json.loads(parent['payload_json'])
            conn.execute('INSERT INTO runtime_definition VALUES (?,?,?,?)', (ident, 'release',
                'ExcelBinOutput/ReliquarySetExcelConfigData.json', dumps({'setId': raw['setId']})))
    def detail(path, set_id, version_line='release'):
        assert Path(path) == DB.path and version_line == 'release'
        return copy.deepcopy(EQUIPMENT['details'][str(set_id)])
    def source(path, affix_id, version_line='release', *, snapshot_id=None):
        assert Path(path) == DB.path and version_line == 'release'
        row = sources[affix_id]
        assert snapshot_id is None or row['snapshot']['snapshot_id'] == snapshot_id
        return copy.deepcopy(row)
    def definition(path, ident, *, version_line='release', sections=()):
        assert Path(path) == DB.path and version_line == 'release'
        assert sections == ('summary', 'references', 'payload')
        return copy.deepcopy(parents[ident])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(queries, 'get_meta', lambda *a, **k: copy.deepcopy(EQUIPMENT['meta']))
        patch.setattr(queries, 'get_artifact_set_detail', detail)
        patch.setattr(equipment_queries, 'get_artifact_set_detail', detail)
        patch.setattr(runtime_queries, 'get_runtime_definition', definition)
        patch.setattr(runtime_queries, 'assemble_runtime_source', source)
        with equipment_queries.equipment_read_session(DB):
            yield
