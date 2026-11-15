"""Public read-only integration checks independent of pure-context internals."""

# ruff: noqa: F811

import copy
import hashlib
import json

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app.cli import main
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime, query_runtime_public
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db  # noqa: F401


def request():
    return {'action': 'action_level_context', 'character_id': 90000001,
            'slot': 'skill', 'constellation': 3}


@pytest.mark.parametrize('field,value', [('approved', True), ('source_authority', 'approved'),
                                       ('action_dispatch', 'ready'), ('unknown', 1)])
def test_public_unknown_permission_inputs_are_rejected(level_db, field, value):
    with pytest.raises(ValueError):
        query_runtime(level_db, {**request(), field: value})


def test_batch_children_cannot_override_snapshot_or_version_line(level_db):
    for fields in ({'snapshot_id': 'different'}, {'version_line': 'beta'}):
        with pytest.raises(ValueError):
            query_runtime(level_db, {'action': 'batch', 'items': [
                {'id': 'source', 'request': {**request(), **fields}}]})


def test_missing_database_query_creates_no_parent(tmp_path):
    database = tmp_path / 'not-created' / 'genshin.db'
    with pytest.raises(RuntimeConfigError):
        query_runtime(database, request())
    assert not database.parent.exists()


def test_pending_context_cli_summary_keeps_level_unavailable_and_dispatch_pending(level_db, fake_repo, capsys):
    path = fake_repo / 'BinOutput/Talent/AvatarTalents/FixtureLevel.json'
    raw = json.loads(path.read_text('utf8'))
    raw['LevelC1'][0]['unknown_callback'] = True
    path.write_text(json.dumps(raw), 'utf8')
    fixture_builder.build_database(level_db.parent, force=True)
    expected = query_runtime(level_db, request())
    assert expected['static_level_status'] == 'pending'
    assert expected['level']['effective_level'] is None
    source = level_db.parent / 'query.json'
    output = level_db.parent / 'context-output.json'
    source.write_text(json.dumps(request()), 'utf8')
    assert main(['--data-dir', str(level_db.parent), 'db', 'runtime', '--input', str(source),
                 '--output', str(output), '--summary']) == 0
    summary = json.loads(capsys.readouterr().out)['data']
    assert summary['static_level_status'] == summary['source_assembly_status'] == 'pending'
    assert summary['level']['effective_level'] is None
    assert summary['action_dispatch'] == {'status': 'pending'}
    assert summary['source_authority'] == 'not_granted'
    saved = json.loads(output.read_text('utf8'))
    assert saved == query_runtime_public(level_db, request())
    assert {key: value for key, value in saved.items() if key != 'owner_publication'} == expected
    assert saved['owner_publication'][0]['entity'] == {'type': 'character', 'id': 90000001}


@pytest.mark.parametrize('target', ['request', 'database'])
def test_cli_output_cannot_overwrite_inputs_even_with_overwrite(level_db, capsys, target):
    source = level_db.parent / 'query.json'
    source.write_text(json.dumps(request()), 'utf8')
    output = source if target == 'request' else level_db
    before_source, before_database = source.read_bytes(), level_db.read_bytes()
    code = main(['--data-dir', str(level_db.parent), 'db', 'runtime', '--input', str(source),
                 '--output', str(output), '--overwrite', '--summary'])
    assert code != 0
    capsys.readouterr()
    assert source.read_bytes() == before_source and level_db.read_bytes() == before_database


def test_new_query_does_not_change_old_level_or_parameter_output(level_db):
    old = {'action': 'action_parameter', 'character_id': 90000001, 'slot': 'skill',
           'talent_level': 10, 'param_index': 0}
    before = copy.deepcopy(query_runtime(level_db, old))
    digest = hashlib.sha256(level_db.read_bytes()).hexdigest()
    query_runtime(level_db, request())
    assert query_runtime(level_db, old) == before
    assert hashlib.sha256(level_db.read_bytes()).hexdigest() == digest
