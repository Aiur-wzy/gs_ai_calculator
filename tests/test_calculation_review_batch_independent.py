"""Root-authored independent review; does not import author test helpers."""
import builtins
import copy
import types
import uuid
from pathlib import Path

import pytest

from genshin_app import calculation_review as single
from genshin_app import calculation_review_batch as batch
from genshin_app import review_batch_json as bounded
from genshin_app.agent_chat import review_projection, tools
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_delivery import delivery_plan
from tests.test_calculation_review import _saved

BASE = Path(__file__).resolve().parents[1]
FROZEN = BASE / 'tests/fixtures/calculation_review_batch/legacy_calculation_review.py.txt'
LEGACY = types.ModuleType('independent_frozen_pages')
exec(compile(FROZEN.read_text(encoding='utf8'), str(FROZEN), 'exec'), LEGACY.__dict__)  # noqa: S102




@pytest.mark.parametrize('view', LEGACY.VIEWS)
def test_old_public_pages_equal_with_combined_filters_and_duplicates(view):
    value = _saved()
    requests = [{'view': view, 'limit': 2, 'offset': 1}, {'view': view, 'limit': 1, 'offset': 999}]
    requests.append(copy.deepcopy(requests[0]))
    before = copy.deepcopy((value, requests))
    out = batch.review_calculation_batch(value, {'requests': requests})
    for row, req in zip(out['pages'], requests, strict=True):
        page = row['review']
        assert page['validation_scope'] == {
            'summary': '旧结果未保存统一验证范围；计算接纳不代表游戏机制完整。',
            'game_mechanism_completeness': 'not_independently_certified'}
        # Only this approved additive field is projected out. All legacy data,
        # counts, arithmetic, paging, and any unexpected fields stay exact.
        assert {key: item for key, item in page.items() if key != 'validation_scope'} == LEGACY.review_calculation(value, req)
    assert (value, requests) == before


@pytest.mark.parametrize('view', ['damage_explanations', 'special_states'])
def test_new_read_views_preserve_legacy_absence_and_batch_single_parity(view):
    # These views did not exist in the frozen public reader. Keep the complete
    # old-view oracle above, and explicitly verify their new absence contract
    # instead of asking an old parser to accept a new protocol discriminator.
    assert set(single.VIEWS) - set(LEGACY.VIEWS) == {'damage_explanations', 'special_states'}
    value = _saved()
    request = {'view': view, 'limit': 2, 'offset': 1}
    requests = [request, copy.deepcopy(request), {**request, 'offset': 999}]
    before = copy.deepcopy((value, requests))
    out = batch.review_calculation_batch(value, {'requests': requests})
    pages = [single.review_calculation(value, req) for req in requests]
    assert [row['review'] for row in out['pages']] == pages
    assert all(page['items'] == [] and page['total_rows'] == 0 for page in pages)
    if view == 'damage_explanations':
        assert all(page['explanation']['status'] == 'missing' for page in pages)
    assert (value, requests) == before


def test_no_snapshot_read_before_all_requests_valid(monkeypatch):
    class Poison(dict):
        def items(self):
            pytest.fail('value accessed before invalid request rejected')
    with pytest.raises(PlanError):
        batch.review_calculation_batch(Poison(), {'requests': [{'view': 'hits'}, {'view': 'unknown'}]})


def test_summary_sort_refuses_exhausted_projection_before_sort(monkeypatch):
    value = _saved()
    value['result']['hits'] = [value['result']['hits'][0]]
    value['result']['hits'][0]['damage_type_ids'] = [f'type.{i:04}' for i in range(4096, 0, -1)]
    value['result']['action_event_compilations'] = []
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    visited, scan_ends = [], []
    original = bounded.prepay
    def record_scan(item, budget, **kwargs):
        size = original(item, budget, **kwargs)
        if budget.role == 'projection' and type(item) is dict and item.get('id') == 'cast.0.1':
            scan_ends.append(budget.used)
        return size
    def watch_sorted(item, *args, **kwargs):
        if type(item) is list and len(item) == 4096:
            visited.append(len(item))
        return builtins.sorted(item, *args, **kwargs)
    monkeypatch.setattr(bounded, 'prepay', record_scan)
    monkeypatch.setattr(single, 'sorted', watch_sorted, raising=False)
    batch.review_calculation_batch(value, {'requests': [{'view': 'summary'}]})
    assert len(scan_ends) == 1 and visited == [4096]
    # A real configured total, rather than mutation during the operation.
    monkeypatch.setattr(batch, 'PROJECTION_WORK', scan_ends[0])
    visited.clear()
    with pytest.raises(PlanError) as caught:
        batch.review_calculation_batch(value, {'requests': [{'view': 'summary'}]})
    assert caught.value.reason == 'review_work_limit'
    assert visited == [], 'damage_type_ids sorting ran after projection budget had no remaining work'


@pytest.fixture
def actual_session(tmp_path):
    plan = delivery_plan()
    hit = plan['manual_template']['hits'][0]
    plan['manual_template']['hits'] = [{**copy.deepcopy(hit), 'id': f'h{i}', 't': 1 + i / 1000} for i in range(60)]
    session = CandidateSession(uuid.uuid4().hex, tmp_path / 'session', tmp_path / 'missing.db', 'native', plan)
    session.calculate(0)
    return session


def invoke(session):
    return session.call_tool('gs_review_result_batch', {'expected_revision': session.revision,
        'request': {'requests': [{'view': 'hits', 'limit': 60}] * 3}})


def test_real_full_result_historical_resource_read_and_reload(actual_session, monkeypatch):
    monkeypatch.setattr(tools, 'MAX_RESPONSE', 8192)
    from genshin_app.equipment import delivery
    original = delivery.validate_delivery_calculated
    calls = []
    def checked(value, **kwargs):
        calls.append(kwargs['_session'])
        return original(value, **kwargs)
    monkeypatch.setattr(delivery, 'validate_delivery_calculated', checked)
    result = invoke(actual_session)
    assert result['code'] == 0, result
    assert len(calls) == 1
    resource = result['data']['reference']['resource']
    assert result['data']['historical_projection'] is True
    pointer = '/projection/pages/0/review/items/0/expected'
    read = actual_session.call_tool('gs_read_context', {'resource': resource, 'pointer': pointer})
    assert read['code'] == 0 and read['data']['value'] == 45
    assert read['data']['original_document']['readable'] is False
    assert read['data']['identity'] == result['data']['identity']
    result['data']['identity']['result_fingerprint'] = '0' * 64
    loaded = CandidateSession.load(actual_session.session_id, actual_session.root, actual_session.db_path)
    again = loaded.call_tool('gs_read_context', {'resource': resource, 'pointer': pointer})
    assert again == read


def test_real_publication_failure_keeps_checkpoint_and_no_ready_reference(actual_session, monkeypatch):
    monkeypatch.setattr(tools, 'MAX_RESPONSE', 8192)
    before = (actual_session.root / 'checkpoint.json').read_bytes()
    def fail(*args):
        raise OSError('independent final checkpoint fault')
    monkeypatch.setattr(review_projection, '_commit_checkpoint', fail)
    reply = invoke(actual_session)
    assert reply['code'] == 1 and reply['reason'] == 'agent_projection_unpublished'
    assert (actual_session.root / 'checkpoint.json').read_bytes() == before
    assert not actual_session.resources and not actual_session._review_projection_records
    assert len(list(actual_session.root.glob('review-*.json'))) == 1
    loaded = CandidateSession.load(actual_session.session_id, actual_session.root, actual_session.db_path)
    assert not loaded.resources
