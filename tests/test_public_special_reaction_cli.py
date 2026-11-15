"""External CLI discovers the same read-only contracts as native tools."""
from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat.agent_hash_input import hash_input
from genshin_app.agent_chat.native_special_reaction_contracts import (
    special_reaction_capabilities,
)
from genshin_app.cli import main


def test_public_cli_exposes_complete_detached_special_contract(capsys):
    assert main(['capabilities']) == 0
    data = json.loads(capsys.readouterr().out)['data']
    assert data['special_reaction_contract'] == special_reaction_capabilities()
    assert data['special_reaction_contract']['qualification_schema']
    assert data['special_reaction_contract']['engine_catalog']['profiles']
    assert data['input_hash_contract']['authority'] == 'none'


@pytest.mark.parametrize('kind',['canonical_json','direct_stellar_damage_profile','direct_lunar_damage_profile'])
def test_cli_and_native_hash_have_identical_technical_meaning(tmp_path, capsys, kind):
    value = {'description':'技术示例，不是游戏事实','profile_content_hash':'0'*64,'ratio':1.0}
    path = tmp_path/'request.json'
    path.write_text(json.dumps({'kind':kind,'value':value},ensure_ascii=False),encoding='utf-8')
    before = path.read_bytes()
    assert main(['engine','hash-input','--input',str(path)]) == 0
    result = json.loads(capsys.readouterr().out)['data']
    assert result == hash_input(kind,value,maximum_bytes=512*1024)
    assert result['authority'] == 'none' and result['game_facts_verified'] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize('text',['{"kind":"canonical_json","kind":"direct_stellar_damage_profile","value":{}}',
    '{"kind":"canonical_json","value":NaN}', '{"kind":"canonical_json"}',
    '{"kind":"arbitrary_algorithm","value":{}}'])
def test_cli_hash_rejects_invalid_requests_without_partial_output(tmp_path,capsys,text):
    path = tmp_path/'request.json'
    path.write_text(text,encoding='utf-8')
    assert main(['engine','hash-input','--input',str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['reason'] == 'agent_tool_input'
