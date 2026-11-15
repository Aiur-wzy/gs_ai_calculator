import copy
import json

import pytest

from genshin_engine.diagnostics import (
    MAX_RESPONSE_BYTES,
    DiagnosticError,
    DiagnosticIndex,
)


def issues(count=1106):
    return [{'code': 'unknown_condition', 'severity': 'pending', 'path': f'r{i%46}',
             'condition': 'state', 'event_id': f'h{i}', 'cycle': i//400} for i in range(count)]


def test_grouping_preserves_all_counts_and_raw_indices_without_requiring_damage():
    raw = issues(); before = copy.deepcopy(raw)
    index = DiagnosticIndex(raw, status='pending', pointer='/details/issues')
    result = index.page()
    assert result['execution_status'] == 'pending'
    assert result['total'] == 46 and sum(g['count'] for g in result['items']) == 1106
    assert result['items'][0]['unknown_phase_count'] == result['items'][0]['count']
    positions = []
    page = index.page(view='issues', limit=200)
    while True:
        positions += [r['index'] for r in page['items']]
        if not page['next_cursor']: break
        page = index.page(view='issues', limit=200, cursor=page['next_cursor'])
    assert positions == list(range(1106)) and raw == before
    raw[0]['cycle'] = 99
    assert index.page(view='issues', filters={'event_id': 'h0'})['items'][0]['excerpt']['cycle'] == 0


def test_unknown_cycle_is_not_assumed_zero_and_path_is_not_inferred_rule():
    result = DiagnosticIndex([{'code': 'x', 'path': 'same-name'}], status='invalid').page()
    row = result['items'][0]
    assert row['cycle_range'] is None and row['unknown_cycle_count'] == 1
    assert 'rule_id' not in row['identity']
    assert result['execution_status'] == 'invalid'


def test_cursor_rejects_source_status_and_filter_changes():
    raw = issues(30); index = DiagnosticIndex(raw, status='pending')
    cursor = index.page(view='issues', limit=2)['next_cursor']
    for other, kwargs in [(DiagnosticIndex(issues(31), status='pending'), {}),
                          (DiagnosticIndex(raw, status='invalid'), {}),
                          (index, {'filters': {'cycle': 0}})]:
        with pytest.raises(DiagnosticError, match='变化'):
            other.page(view='issues', cursor=cursor, **kwargs)


def test_bounded_summary_clips_only_excerpt_and_returns_exact_raw_pointer():
    raw = [{'code': f'code{i}', 'message': 'x'*9000, 'evidence': ['y'*9000]*8} for i in range(60)]
    result = DiagnosticIndex(raw, status='pending').page(limit=200)
    assert len(json.dumps(result, ensure_ascii=False).encode()) < MAX_RESPONSE_BYTES
    assert all(not r['examples'][0]['excerpt_complete'] for r in result['items'])
    assert result['items'][0]['examples'][0]['json_pointer'].startswith('/issues/')
    assert raw[0]['message'] == 'x'*9000


def test_exact_filters_do_not_merge_missing_or_other_typed_values():
    raw = issues(5); raw[0].pop('cycle')
    index = DiagnosticIndex(raw, status='pending')
    assert index.page(filters={'cycle': 0})['matched_issue_count'] == 4
    with pytest.raises(DiagnosticError): index.page(filters={'cycle': False})
    with pytest.raises(DiagnosticError): index.page(filters={'guessed_rule': 'r0'})
    gid = index.page(view='issues', filters={'event_id': 'h3'})['items'][0]['group_id']
    assert index.page(view='issues', filters={'group_id': gid})['items'][0]['index'] == 3
