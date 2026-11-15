"""Independent actual CLI/session query boundaries; no model transport is created."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.whiteboard import new_whiteboard
from genshin_db import equipment_queries, queries
from tests.test_calculation_delivery import delivery_plan

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT/'data/genshin.db'


@pytest.fixture(scope='module')
def cli(accepted_current_only_cli):
    return accepted_current_only_cli


def request(tmp_path, value):
    path = tmp_path/'request.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def make_session(tmp_path, workflow, db=DB):
    if workflow == 'build':
        return BoardCandidateSession(uuid.uuid4().hex, tmp_path/'session', db, 'whiteboard', new_whiteboard())
    return CandidateSession(uuid.uuid4().hex, tmp_path/'session', db, 'native', delivery_plan())


def test_real_capabilities_expose_two_closed_commands_and_contracts(cli):
    status, envelope = cli('capabilities')
    assert status == 0
    data = envelope['data']
    commands = {command['name']: command for command in data['commands']}
    for name in ('db catalog', 'db panel-basis'):
        assert name in commands
        assert {arg['name'] for arg in commands[name]['args']} == {'--input', '--output', '--overwrite'}
    assert data['catalog_contract']['database_read_only']
    assert data['panel_basis_contract']['levels_with_data'] == [90, 100]


@pytest.mark.parametrize('level,expected', [(90, 334.84974830517604), (100, 410.18625468762184)])
def test_real_cli_panel_uses_distinct_level_columns_and_explicit_weapon(tmp_path, level, expected, cli, accepted_source_data_dir):
    db = accepted_source_data_dir / 'genshin.db'
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    path = request(tmp_path, {'db_character_id': 10000016, 'level': level,
                              'weapon': {'weapon_id': 12514, 'level': 90, 'ascension': 6}})
    status, envelope = cli('db', 'panel-basis', '--input', path)
    assert status == 0
    data = envelope['data']
    assert data['state'] == 'ready' and data['character_base_atk'] == expected
    assert data['panel_base']['base_atk'] == data['character_base_atk'] + data['weapon_contribution']['base_atk']
    assert data['source_refs'][0]['fields'] == [f'{stat}_{level}' for stat in ('atk', 'hp', 'def')]
    assert not {'atk', 'hp', 'def', 'em', 'crit_rate', 'crit_dmg'} & data.keys()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('value,code', [
    ({'db_character_id': 10000016}, 'weapon_selection_required'),
    ({'db_character_id': 10000016, 'level': 80}, 'unsupported_character_level'),
    ({'db_character_id': 999999999}, 'character_unavailable'),
    ({'db_character_id': 10000016, 'weapon': {'weapon_id': 12514}}, 'weapon_state_required'),
])
def test_cli_unknown_basis_preserved_instead_of_fallback(tmp_path, value, code, cli):
    status, envelope = cli('db', 'panel-basis', '--input', request(tmp_path, value))
    assert status == 0
    data = envelope['data']
    assert data['panel_base']['base_atk'] is None
    assert code in {item['code'] for item in data['missing_inputs']}


def test_cli_real_file_delivery_is_complete_and_overwrite_is_explicit(tmp_path, cli):
    path = request(tmp_path, {'kind': 'character', 'query': '迪卢克'})
    output = tmp_path/'result.json'
    status, envelope = cli('db', 'catalog', '--input', path, '--output', output)
    assert status == 0
    delivered = json.loads(output.read_text('utf-8'))
    assert delivered['items'][0]['id'] == 10000016 and delivered['snapshot']['fingerprint']
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    assert envelope['data']['file_shas'] == {str(output.resolve()): sha}
    assert cli('db', 'catalog', '--input', path, '--output', output)[0] == 1
    assert hashlib.sha256(output.read_bytes()).hexdigest() == sha
    assert cli('db', 'catalog', '--input', path, '--output', output, '--overwrite')[0] == 0
    assert hashlib.sha256(output.read_bytes()).hexdigest() == sha


@pytest.mark.parametrize('target', ['request', 'genshin.db', '.genshin.db.write.lock', 'genshin.db-wal',
                                    'genshin.db-shm', 'genshin.db-journal'])
def test_real_cli_protects_inputs_and_database_sidecars_before_query(tmp_path, target, cli):
    path = request(tmp_path, {'kind': 'character', 'query': '迪卢克'})
    directory = tmp_path/'fake-data'
    directory.mkdir()
    output = path if target == 'request' else directory/target
    if target != 'request':
        output.write_bytes(b'protected original content')
    before = output.read_bytes()
    status, envelope = cli('db', 'catalog', '--input', path, '--output', output, '--overwrite', data=directory)
    assert status == 1 and envelope['message']
    assert output.read_bytes() == before


@pytest.mark.parametrize('workflow', ['build', 'native'])
def test_session_catalog_matches_cli_snapshot_and_shares_read_transaction(
        tmp_path, monkeypatch, workflow, cli, accepted_source_data_dir):
    session = make_session(tmp_path, workflow, accepted_source_data_dir / 'genshin.db')
    original = copy.deepcopy(session.candidate)
    observations = []
    for name in ('get_meta', 'list_characters'):
        function = getattr(queries, name)

        def observed(*args, _function=function, _name=name, **kwargs):
            current = equipment_queries._SESSION.get()
            assert current is not None
            observations.append((_name, id(current['conn'])))
            return _function(*args, **kwargs)

        monkeypatch.setattr(queries, name, observed)
    tool = session.call_tool('gs_search_catalog', {'kind': 'character', 'query': '迪卢克'})
    assert tool['code'] == 0, tool
    assert len({identity for _, identity in observations}) == 1
    assert {'get_meta', 'list_characters'} <= {name for name, _ in observations}
    status, envelope = cli('db', 'catalog', '--input', request(tmp_path, {'kind': 'character', 'query': '迪卢克'}))
    assert status == 0 and tool['data'] == envelope['data']
    assert session.candidate == original and session.revision == 0 and session.calculated is None


@pytest.mark.parametrize('workflow', ['build', 'native'])
@pytest.mark.parametrize('name,args', [
    ('gs_search_catalog', {'kind': 'character', 'query': '迪卢克'}),
    ('gs_character_panel_basis', {'db_character_id': 10000016}),
])
def test_host_version_line_cannot_be_overridden_by_model_arguments(tmp_path, workflow, name, args):
    session = make_session(tmp_path, workflow)
    original = copy.deepcopy(session.candidate)
    rejected = session.call_tool(name, {**args, 'version_line': 'beta'})
    assert rejected['code'] == 1 and rejected['reason'] == 'agent_tool_input'
    accepted = session.call_tool(name, args)
    assert accepted['code'] == 0 and accepted['data']['version_line'] == 'release'
    assert session.candidate == original and session.revision == 0


def test_missing_database_cli_does_not_create_directory(tmp_path, cli):
    directory = tmp_path/'never-created'
    status, _ = cli('db', 'catalog', '--input', request(tmp_path, {'kind': 'character', 'query': '迪卢克'}),
                    data=directory)
    assert status == 1 and not directory.exists()
