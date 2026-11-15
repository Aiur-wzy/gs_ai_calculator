"""Actual manager/candidate batch route; only the provider transport is simulated."""
from __future__ import annotations

import copy

import pytest

from genshin_app import calculation_review_batch as batch
from tests.test_agent_integration import corrected_hit
from tests.test_agent_integration import manager as _manager
from tests.test_agent_manager import finish, request
from tests.test_calculation_delivery import delivery_plan

manager = _manager


def test_manager_dispatches_batch_review_of_actual_calculated_candidate(manager, monkeypatch):
    original = delivery_plan()
    before = copy.deepcopy(original)
    transport = manager._transport
    queries = [{'view': 'summary'}, {'view': 'hits'}, {'view': 'hits'}]
    transport.scripts = [corrected_hit(0, 2)[:2] + [('gs_review_result_batch', {
        'expected_revision': 1, 'request': {'requests': queries}})]]
    calls = []
    execute = batch._review_complete_calculated

    def recorded(value, query, **kwargs):
        calls.append(query)
        return execute(value, query, **kwargs)

    monkeypatch.setattr(batch, '_review_complete_calculated', recorded)
    sid = manager.start(request(document=original))['session_id']
    finish(manager)
    assert manager.poll(sid)['state'] == 'completed'
    assert all(reply['code'] == 0 for _, reply in transport.replies), transport.replies
    assert calls == [{'requests': queries}]
    output = transport.replies[-1][1]['data']
    assert output['historical_projection'] is True and output['original_revision'] == 1
    pages = [page['review'] for page in output['pages']]
    assert [page['view'] for page in pages] == ['summary', 'hits', 'hits']
    assert pages[1] == pages[2]
    assert pages[0]['items'][0]['expected'] == pages[1]['items'][0]['expected'] == pytest.approx(90)
    current = manager.candidate(sid)
    assert current['ready'] and current['revision'] == 1
    assert output['identity']['result_fingerprint'] == manager._candidates[sid].calculated['result_receipt']['result_fingerprint']
    assert original == before
