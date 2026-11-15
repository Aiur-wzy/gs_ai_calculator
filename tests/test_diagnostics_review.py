"""Independent pure-index review; raw evidence and execution meaning stay intact."""
import copy
import json

import pytest

from genshin_engine.diagnostics import (
    MAX_RESPONSE_BYTES,
    DiagnosticError,
    DiagnosticIndex,
)


def test_cycle_filter_does_not_treat_boolean_or_float_as_known_integer_cycle():
    raw=[{'code':'x','event_id':str(i),'cycle':v} for i,v in enumerate((0,False,0.0,1))]
    index=DiagnosticIndex(raw,status='pending')
    page=index.page(view='issues',filters={'cycle':0})
    assert [row['index'] for row in page['items']]==[0]


def test_cursor_offset_beyond_selected_total_is_rejected():
    index=DiagnosticIndex([{'code':'x','event_id':str(i)} for i in range(4)],status='invalid')
    cursor=index.page(view='issues',limit=1)['next_cursor']
    cursor['offset']=1000
    with pytest.raises(DiagnosticError):
        index.page(view='issues',cursor=cursor)


def test_byte_limited_group_pagination_keeps_every_group_and_raw_index():
    raw=[{'code':str(i),'severity':'pending','message':'文'*10000,
          'nested':{'detail':['原'*10000]*8},'event_id':str(i)} for i in range(70)]
    index=DiagnosticIndex(raw,status='pending',pointer='/details/execution/issues')
    cursor=None; returned=[]
    while True:
        page=index.page(limit=200,cursor=cursor)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode('utf8'))<=MAX_RESPONSE_BYTES
        assert page['total']==70 and page['matched_issue_count']==70
        returned.extend(g['first_issue_index'] for g in page['items'])
        cursor=page['next_cursor']
        if cursor is None:break
    assert sorted(returned)==list(range(70))


def test_readonly_input_and_returned_excerpt_do_not_mutate_future_pages():
    raw=[{'code':'x','details':{'original':['a','b']},'event_id':'h'}]
    before=copy.deepcopy(raw)
    index=DiagnosticIndex(raw,status='pending')
    page=index.page(view='issues')
    page['items'][0]['excerpt']['details']['original'][0]='CHANGED'
    raw[0]['event_id']='CHANGED'
    later=index.page(view='issues')
    assert later['items'][0]['excerpt']==before[0]


def test_group_identity_keeps_source_effect_condition_and_unknown_separate():
    raw=[{'code':'x','path':'looks-like-rule','condition':'hit','effect_id':'e1','source_id':'s'},
         {'code':'x','path':'looks-like-rule','condition':'hp','effect_id':'e1','source_id':'s'},
         {'code':'x','path':'looks-like-rule','condition':'hit','effect_id':'e2','source_id':'s'},
         {'code':'x','path':'looks-like-rule','condition':'hit','effect_id':'e1','source_id':'other'}]
    page=DiagnosticIndex(raw,status='unproven').page()
    assert page['total']==4
    assert all('rule_id' not in g['identity'] for g in page['items'])
    assert all(g['unknown_cycle_count']==1 and g['cycle_range'] is None for g in page['items'])


def test_filtered_group_counts_are_selected_counts_and_cursor_cannot_change_filter():
    raw=[{'code':'x','event_id':str(i),'cycle':i%2} for i in range(9)]
    index=DiagnosticIndex(raw,status='pending')
    page=index.page(filters={'cycle':1})
    assert page['matched_issue_count']==4 and page['items'][0]['count']==4
    cursor=index.page(view='issues',filters={'cycle':1},limit=1)['next_cursor']
    with pytest.raises(DiagnosticError):
        index.page(view='issues',filters={'cycle':0},cursor=cursor)


def test_long_unicode_signature_has_stable_id_without_changing_original_evidence():
    condition='条件'*1000
    raw=[{'code':'x','condition':condition,'message':'文'*1000}]
    first=DiagnosticIndex(raw,status='pending').page()
    reordered=DiagnosticIndex([dict(reversed(list(raw[0].items())))],status='pending').page()
    assert first['items'][0]['group_id']==reordered['items'][0]['group_id']
    assert first['items'][0]['identity_complete'] is False
    assert raw[0]['condition']==condition


def test_empty_execution_still_has_status_and_zero_count():
    page=DiagnosticIndex([],status='invalid',pointer='/details/issues').page()
    assert page['execution_status']=='invalid'
    assert page['raw_issue_count']==0 and page['items']==[]
    assert page['next_cursor'] is None
