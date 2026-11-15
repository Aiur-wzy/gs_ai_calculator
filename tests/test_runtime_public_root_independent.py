"""Independent tiny-source importer/query counterexamples, no main DB access."""
import hashlib
import json
import sqlite3
import zlib
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db.runtime_public_root import get_public_root_catalog
from genshin_db.runtime_queries import query_runtime
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build

SOURCE = 'BinOutput/Common/ConfigGlobalCombat.json'


def raw_value():
    return {'能力组': ['First', 'First', None, '', False, 0, 0.0], 'unknown': {'future': [3, 2, 1]},
            'abilityName': 'Trap_Ability', '$type': 'AddAbility',
            'onAdded': [{'$type': 'GenerateElemBall', 'configID': 2022}],
            'modifiers': {'Trap': {'duration': 99}}, 'openConfig': 'Trap', 'paramSpecial': 'N',
            'nested': {'$type': 'ModifyAbility', 'abilityName': 'Trap_Ability', 'paramDelta': '%1'}}


@pytest.fixture
def small(fake_repo, tmp_path):
    (fake_repo / SOURCE).write_bytes(json.dumps(raw_value(), ensure_ascii=False, indent=2).encode('utf8'))
    db = tmp_path / 'independent-public.db'
    build(db, fake_repo)
    return db, fake_repo


def revalue_table_counts(conn):
    report = json.loads(conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()[0])
    for table in report['table_counts']:
        report['table_counts'][table] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE version_line=?', ('release',)).fetchone()[0]
    conn.execute("UPDATE data_component SET validation_json=? WHERE component='runtime_config' AND version_line='release'", (json.dumps(report),))


def test_raw_unknown_and_executable_looking_members_never_create_any_semantic_edges(small):
    db, _ = small
    before = db.read_bytes()
    result = query_runtime(db, {'action': 'public_root_catalog'})
    assert result['root']['raw'] == raw_value()
    assert result['execution_authority'] is False and result['mounted_ability_roots'] is None
    assert result['role_review_status'] == result['mount_status'] == result['activation_eligibility'] == 'not_evaluated'
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute('SELECT definition_id,definition_kind,json_pointer FROM runtime_definition WHERE source_path=?', (SOURCE,)).fetchall()
        assert len(rows) == 1 and rows[0][1:] == ('global_combat_config', '')
        for table in ('runtime_reference', 'runtime_object_reference', 'runtime_material_reference'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE source_path=?', (SOURCE,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM runtime_parameter_binding WHERE origin_definition_id=?', (rows[0][0],)).fetchone()[0] == 0
    assert db.read_bytes() == before


def test_lf_crlf_bom_original_bytes_and_unknown_structure_all_survive(small):
    db, repo = small
    text = json.dumps(raw_value(), ensure_ascii=False, indent=2)
    snapshots, hashes = [], []
    for raw in (text.encode(), text.replace('\n', '\r\n').encode(), b'\xef\xbb\xbf' + text.encode()):
        (repo / SOURCE).write_bytes(raw)
        built = build(db, repo)
        result = get_public_root_catalog(db)
        snapshots.append(built['snapshot_id'])
        hashes.append(result['root']['source_sha256'])
        assert result['root']['raw'] == raw_value()
        assert hashes[-1] == hashlib.sha256(raw).hexdigest()
        with closing(sqlite3.connect(db)) as conn:
            original = zlib.decompress(conn.execute('SELECT payload_blob FROM runtime_source WHERE source_path=?', (SOURCE,)).fetchone()[0])
        assert original == raw
    assert len(set(snapshots)) == len(set(hashes)) == 3


def test_sibling_common_file_is_not_imported_as_a_fallback(small):
    db, repo = small
    sibling = repo / 'BinOutput/Common/ConfigGlobalCombatCopy.json'
    sibling.write_bytes((repo / SOURCE).read_bytes())
    manifest = catalog.enumerate_runtime_sources(repo)
    assert sibling.relative_to(repo).as_posix() not in manifest['files']
    (repo / SOURCE).unlink()
    report = build(db, repo)
    assert report['source_capability_state'] == 'degraded'
    result = get_public_root_catalog(db)
    assert result['status'] == 'missing'
    assert result['reason'] == 'public_source_unavailable'
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            'SELECT COUNT(*) FROM runtime_source WHERE source_path IN (?,?)',
            (SOURCE, sibling.relative_to(repo).as_posix()),
        ).fetchone()[0] == 0


@pytest.mark.parametrize('payload', [b'{"x":1,"x":2}', b'{"x":NaN}', b'false', b'[{}]', b''])
def test_invalid_new_public_input_cannot_replace_existing_good_database(small, payload):
    db, repo = small
    before = db.read_bytes()
    (repo / SOURCE).write_bytes(payload)
    with pytest.raises(catalog.RuntimeConfigError):
        build(db, repo)
    assert db.read_bytes() == before


@pytest.mark.parametrize('kind', ['payload', 'size', 'family', 'missing_source', 'root_pointer'])
def test_inconsistent_saved_public_source_fails_closed(small, kind):
    db, _ = small
    with closing(sqlite3.connect(db)) as conn, conn:
        if kind == 'payload': conn.execute('UPDATE runtime_source SET payload_blob=? WHERE source_path=?', (zlib.compress(b'{}'), SOURCE))
        elif kind == 'size': conn.execute('UPDATE runtime_source SET source_size_bytes=source_size_bytes+1 WHERE source_path=?', (SOURCE,))
        elif kind == 'family': conn.execute("UPDATE runtime_source SET source_family='ability' WHERE source_path=?", (SOURCE,))
        elif kind == 'missing_source': conn.execute('DELETE FROM runtime_source WHERE source_path=?', (SOURCE,))
        elif kind == 'root_pointer': conn.execute("UPDATE runtime_definition SET json_pointer='/unknown' WHERE source_path=?", (SOURCE,))
    with pytest.raises(catalog.RuntimeConfigError): get_public_root_catalog(db)


def test_public_root_cannot_share_its_source_with_a_forged_executable_definition(small):
    db, _ = small
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute('SELECT * FROM runtime_definition WHERE source_path=?', (SOURCE,)).fetchone())
        row.update(definition_id='a' * 64, definition_kind='ability', exact_key_json='"Trap_Ability"',
                   namespace_key='Common', summary_json='{}')
        conn.execute('INSERT INTO runtime_definition VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        conn.execute('UPDATE runtime_source SET indexed_definition_count=2 WHERE source_path=?', (SOURCE,))
        revalue_table_counts(conn)
    with pytest.raises(catalog.RuntimeConfigError):
        get_public_root_catalog(db)


def test_closed_query_contract_cannot_accept_requested_mount_or_actor(small):
    db, _ = small
    for extra in ({'character_id': 10000063}, {'mounted': True}, {'authority': 'approved'}):
        with pytest.raises(ValueError):
            query_runtime(db, {'action': 'public_root_catalog', **extra})


@pytest.mark.parametrize('table', ['runtime_reference', 'runtime_object_reference', 'runtime_material_reference'])
def test_extra_edge_claiming_public_source_cannot_hide_behind_other_origin_definition(small, table):
    db, _ = small
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute('SELECT * FROM runtime_reference LIMIT 1').fetchone())
        row.update(reference_id='b' * 64, source_path=SOURCE)
        if table != 'runtime_reference': row['legacy_reference_id'] = None
        conn.execute(f'INSERT INTO {table} (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        revalue_table_counts(conn)
    with pytest.raises(catalog.RuntimeConfigError): get_public_root_catalog(db)


def test_extra_parameter_binding_under_forged_same_source_talent_is_rejected(small):
    db, _ = small
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        definition = dict(conn.execute('SELECT * FROM runtime_definition WHERE source_path=?', (SOURCE,)).fetchone())
        definition.update(definition_id='c' * 64, definition_kind='talent_config', exact_key_json='"forged"')
        conn.execute('INSERT INTO runtime_definition VALUES (' + ','.join('?' for _ in definition) + ')', tuple(definition.values()))
        binding = dict(conn.execute('SELECT * FROM runtime_parameter_binding LIMIT 1').fetchone())
        binding.update(binding_id='d' * 64, origin_definition_id=definition['definition_id'], target_reference_id=None)
        conn.execute('INSERT INTO runtime_parameter_binding VALUES (' + ','.join('?' for _ in binding) + ')', tuple(binding.values()))
        conn.execute('UPDATE runtime_source SET indexed_definition_count=2 WHERE source_path=?', (SOURCE,))
        revalue_table_counts(conn)
    with pytest.raises(catalog.RuntimeConfigError): get_public_root_catalog(db)

# ruff: noqa: F811 - pytest fixture imports are injected by name.
