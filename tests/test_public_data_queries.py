"""Public-data query limits, unknown inputs and protected publication."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app import public_data_query as lookup
from genshin_db import catalog_query as catalog


@pytest.mark.parametrize('patch', [
    {'kind': []}, {'kind': 'skill'}, {'query': None}, {'query': ' '}, {'query': 'a'*129},
    {'page': True}, {'page': 10001}, {'page_size': 31}, {'page_size': 0}, {'version_line': None}, {'extra': 1},
    {'query': ' '+ 'a'*128},
])
def test_catalog_closed_request(patch):
    with pytest.raises(ValueError):
        catalog.validate_catalog_request({'kind':'character','query':'神里',**patch})


def test_missing_db_cannot_create_directory(tmp_path):
    root = tmp_path/'does-not-exist'
    with pytest.raises(FileNotFoundError):
        catalog.query_catalog(root/'genshin.db', {'kind':'character','query':'迪卢克'})
    assert not root.exists()


def test_catalog_tool_and_shared_helper_have_same_text_limit():
    from genshin_app.agent_chat.tools import SPECS
    schema = SPECS['gs_search_catalog'][1]['properties']['query']
    assert schema['maxLength'] == catalog.LIMITS['query_chars']
    normalized = catalog.validate_catalog_request({'kind': 'character', 'query': ' 神里 '})
    assert normalized['query'] == '神里'


@pytest.mark.parametrize('weapon', [{'unknown': 1}, {'weapon_id': True}, {'level': 101}, {'ascension': 7}])
def test_panel_weapon_closed(weapon):
    with pytest.raises(ValueError):
        lookup.validate_panel_request({'db_character_id':1, 'weapon':weapon})


def test_panel_unsupported_level_and_partial_weapon_preserved():
    value = lookup.validate_panel_request({'db_character_id':1, 'level':80, 'weapon':{'weapon_id':2}})
    assert value['level']==80 and value['weapon']=={'weapon_id':2}


def args(tmp_path, request):
    path=tmp_path/'request.json'
    path.write_text(json.dumps(request),'utf8')
    data_dir = tmp_path/'data'
    data_dir.mkdir()
    (data_dir/'genshin.db').touch()
    return SimpleNamespace(input=str(path), output=None, data_dir=str(data_dir), overwrite=False, _lookup='catalog')


def test_output_rejects_request_and_database_hardlink(tmp_path):
    value=args(tmp_path, {'kind':'character','query':'x'})
    before=Path(value.input).read_bytes()
    value.output=value.input
    with pytest.raises(ValueError):lookup.run_lookup(value, catalog_function=lambda *_: {})
    assert Path(value.input).read_bytes()==before
    database=Path(value.data_dir)/'genshin.db'
    database.write_bytes(b'protected db')
    alias=tmp_path/'alias.json'
    try:
        alias.hardlink_to(database)
    except OSError as exc:
        pytest.skip(f'当前测试卷或ACL不允许创建硬链接：{exc}')
    value.output=str(alias)
    with pytest.raises(ValueError):lookup.run_lookup(value, catalog_function=lambda *_: {})
    assert database.read_bytes()==b'protected db'


def test_oversized_output_is_not_published(tmp_path):
    value=args(tmp_path, {'kind':'character','query':'x'});value.output=str(tmp_path/'out.json')
    with pytest.raises(ValueError):
        lookup.run_lookup(value, catalog_function=lambda *_: {'version_line':'release','data':'x'*262144})
    assert not Path(value.output).exists()


def test_file_output_no_overwrite_and_valid_json(tmp_path):
    value=args(tmp_path, {'kind':'character','query':'x'});value.output=str(tmp_path/'out.json')
    data={'version_line':'release','items':[]}
    receipt=lookup.run_lookup(value,catalog_function=lambda *_: data)
    assert json.loads(Path(value.output).read_text('utf8'))==data
    assert receipt['published_paths']
    with pytest.raises(ValueError):lookup.run_lookup(value,catalog_function=lambda *_: data)


def test_bounded_input_before_parse(tmp_path):
    value=args(tmp_path, {'huge':'x'*8192})
    with pytest.raises(ValueError,match='8KiB'):
        lookup.run_lookup(value,catalog_function=lambda *_: {})
