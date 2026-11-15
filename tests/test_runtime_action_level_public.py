"""Public source context retains pending execution through query, batch and CLI."""
import copy
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app.cli import main
from genshin_db.runtime_action_level import get_runtime_action_level_context
from genshin_db.runtime_queries import (
    query_runtime,
    query_runtime_public,
    runtime_capabilities,
)
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db  # noqa: F401

# ruff: noqa: F811


def request():
    return {'action':'action_level_context', 'character_id':90000001,
            'slot':'skill', 'constellation':6}


def test_public_context_and_batch_retain_exact_same_snapshot(level_db):
    req = request()
    original = copy.deepcopy(req)
    data = query_runtime_public(level_db, req)
    profile = dict(data)
    publication = profile.pop('owner_publication')
    assert profile == get_runtime_action_level_context(level_db, character_id=90000001,
                                                       slot='skill', constellation=6)
    assert profile == query_runtime(level_db, req)
    assert len(publication) == 1
    assert publication[0]['entity'] == {'type': 'character', 'id': 90000001}
    assert publication[0]['entity_status'] == 'unknown'
    assert publication[0]['requires_usage_confirmation'] is True
    assert req == original and data['level']['effective_level'] == 13
    parameter = {**req, 'action':'action_parameter_at_level', 'param_index':0}
    result = query_runtime_public(level_db, {'action':'batch', 'snapshot_id':data['snapshot']['snapshot_id'],
        'items':[{'id':'context','request':req}, {'id':'parameter','request':parameter}]})
    assert result['status'] == 'complete'
    assert result['items'][0]['data'] == data
    assert result['items'][1]['data'] == query_runtime_public(level_db, parameter)
    assert all(item['data']['snapshot'] == result['snapshot'] for item in result['items'])
    assert data['action_dispatch'] == 'pending' and data['source_authority'] == 'not_granted'


def test_capabilities_separate_source_and_execution():
    caps = runtime_capabilities()
    assert caps['required']['action_level_context'] == ['character_id']
    assert 'action_level_context' in caps['batch']['actions']
    assert caps['action_level_context']['action_dispatch'] == 'pending'
    assert caps['action_level_context']['operation_execution'] == 'not_evaluated'
    assert 'talent_level' not in caps['actions']['action_level_context']


@pytest.mark.parametrize('fields', [{'talent_level':13}, {'effective_level':13},
                                   {'base_level':13}, {'character_id':None}])
def test_public_rejects_effective_level_or_missing_character(level_db, fields):
    with pytest.raises(ValueError):
        query_runtime_public(level_db, {**request(), **fields})


def test_cli_real_query_saves_complete_file_and_distinct_states(level_db, capsys):
    req = request()
    expected = query_runtime_public(level_db, req)
    request_path, output = level_db.parent/'request.json', level_db.parent/'context.json'
    request_path.write_text(json.dumps(req), 'utf8')
    assert main(['--data-dir',str(level_db.parent),'db','runtime','--input',str(request_path),
                 '--output',str(output),'--summary']) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)['data']
    assert not captured.err and len(captured.out.encode('utf8')) < 4000
    assert json.loads(output.read_text('utf8')) == expected
    assert summary['static_level_status'] == summary['source_assembly_status'] == 'ready'
    assert summary['footprint_status'] == 'target_identified'
    assert summary['action_dispatch'] == {'status':'pending'}
    assert summary['whole_skill_coverage'] == 'partial'
    assert summary['footprints'] == {'total':6,'target_identified':6,'pending':0}
    assert summary['files']['file_shas'][str(output.resolve())] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_agent_query_uses_pinned_read_and_persists_complete_context(level_db, monkeypatch):
    from genshin_app.agent_chat import tools
    entered, saved, writes = [], [], []
    @contextmanager
    def pinned():
        entered.append(True)
        yield
    session = SimpleNamespace(db_path=level_db, root=level_db.parent, resources={},
                              _read_db=pinned, save=lambda:saved.append(True))
    monkeypatch.setattr(tools, 'write_owned', lambda root,name,value,limit:writes.append((name,copy.deepcopy(value))))
    tools._query(session, {'request':request()}, query_runtime_public)
    assert entered == [True] and saved == [True]
    assert writes == [('query-1.json',query_runtime_public(level_db,request()))]
    assert session.resources['query-1']['action_dispatch'] == 'pending'
