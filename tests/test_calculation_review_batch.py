import copy
import json
import sys
import threading
import types
from pathlib import Path

import pytest

from genshin_app import calculation_review as single
from genshin_app import calculation_review_batch as batch
from genshin_app import review_batch_json as bounded
from genshin_app.equipment.delivery import (
    PACKAGE_KIND,
    PACKAGE_SCHEMA,
    validate_delivery_calculated,
)
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import run_plan_calc
from tests.resource_reaction_review_helpers import count_engine, live_case, resign
from tests.test_calculation_delivery import delivery_plan
from tests.test_calculation_review import _saved
from tests.test_resource_reaction_app import reaction_plan
from tests.test_shared_pool_app_delivery import authorize

ROOT = Path(__file__).resolve().parents[1]
LEGACY = types.ModuleType('frozen_review_oracle')
FROZEN = ROOT / 'tests/fixtures/calculation_review_batch/legacy_calculation_review.py.txt'
exec(compile(FROZEN.read_text(encoding='utf-8'), str(FROZEN), 'exec'), LEGACY.__dict__)  # noqa: S102 - fixed read-only frozen oracle; avoid writing its pycache


def _legacy_with_scope(value, request):
    """Keep the frozen reader unchanged; add only the approved scope contract."""
    expected = LEGACY.review_calculation(value, request)
    result = value.get('calculated', value)['result']
    expected['validation_scope'] = copy.deepcopy(result.get('validation_scope', {
        'summary': '旧结果未保存统一验证范围；计算接纳不代表游戏机制完整。',
        'game_mechanism_completeness': 'not_independently_certified'}))
    return expected


def test_cold_imports_are_all_candidate_sources():
    expected = (ROOT / 'src').resolve()
    for name, module in list(sys.modules.items()):
        if name.startswith(('genshin_app', 'genshin_db', 'genshin_engine', 'genshin_runtime')) and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(expected), (name, module.__file__)


def test_all_views_exact_frozen_single_semantics():
    value = _saved()
    legacy_views = [view for view in single.VIEWS if view in LEGACY.VIEWS]
    for start in (0, 16):
        queries = [{'view': view, 'limit': 7, 'offset': 1} for view in legacy_views[start:start + 16]]
        output = batch.review_calculation_batch(value, {'requests': queries})
        for index, query in enumerate(queries):
            assert output['pages'][index] == {'page_index': index, 'review': _legacy_with_scope(value, query)}


@pytest.mark.parametrize('kind', ['income', 'zero', 'no-match', 'excluded', 'full-pool', 'cooldown', 'failed-prefix'])
def test_real_reaction_views_exact_frozen_single(tmp_path, kind):
    complete = live_case(tmp_path, kind)
    value = {key:complete[key] for key in ('result','result_receipt')}
    queries = [{'view': 'resource_reactions'}, {'view': 'hits'}, {'view': 'summary'},
               {'view': 'resource_reactions', 'filters': {'event_id': 'absent'}}]
    actual = batch.review_calculation_batch(value, {'requests': queries})
    assert [p['review'] for p in actual['pages']] == [_legacy_with_scope(value, q) for q in queries]


@pytest.mark.parametrize('scope', [None, {}, {
    'summary': '仅证明输入内已执行内容',
    'game_mechanism_completeness': 'not_independently_certified',
    'not_checked': ['未提交的效果', '游戏资料完整性'],
}])
def test_validation_scope_is_additive_preserved_and_detached(scope):
    value = _saved()
    if scope is not None:
        value['result']['validation_scope'] = copy.deepcopy(scope)
        value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    before = copy.deepcopy(value)
    queries = [{'view': 'summary'}, {'view': 'hits', 'offset': 1, 'limit': 2}]
    actual = batch.review_calculation_batch(value, {'requests': queries})
    expected_scope = scope if scope is not None else {
        'summary': '旧结果未保存统一验证范围；计算接纳不代表游戏机制完整。',
        'game_mechanism_completeness': 'not_independently_certified'}
    for row, query in zip(actual['pages'], queries, strict=True):
        page = row['review']
        assert page['validation_scope'] == expected_scope
        assert {key: item for key, item in page.items() if key != 'validation_scope'} == LEGACY.review_calculation(value, query)
    actual['pages'][0]['review']['validation_scope']['summary'] = 'modified returned copy'
    if scope:
        actual['pages'][0]['review']['validation_scope']['not_checked'].append('modified child')
    assert actual['pages'][1]['review']['validation_scope'] == expected_scope
    assert value == before


def test_batch_owns_exactly_one_unpack_and_engine_execution(tmp_path, monkeypatch):
    complete = live_case(tmp_path)
    value = {key:complete[key] for key in ('result','result_receipt')}
    queries = [{'view': 'resource_reactions', 'limit': 1}] * 12
    calls = count_engine(monkeypatch)
    unpacked, sessions = [], []
    original_unpack, original_init = single._unpack, ExecutionVerificationSession.__init__

    def unpack(*args, **kwargs):
        unpacked.append(kwargs['_session'])
        return original_unpack(*args, **kwargs)

    def init(self):
        original_init(self)
        sessions.append(self)

    monkeypatch.setattr(single, '_unpack', unpack)
    monkeypatch.setattr(ExecutionVerificationSession, '__init__', init)
    first = batch.review_calculation_batch(value, {'requests': queries})
    assert len(calls) == len(unpacked) == len(sessions) == 1
    assert sessions[0]._closed and not sessions[0]._cache
    assert sessions[0]._usage['executions'] == 1
    second = batch.review_calculation_batch(value, {'requests': queries})
    assert first == second and len(calls) == 2 and sessions[0] is not sessions[1]
    value['result']['hits'][0]['expected'] += 1
    with pytest.raises(PlanError):
        batch.review_calculation_batch(value, {'requests': queries})
    assert sessions[-1]._closed


def test_batch_window_and_resource_validators_keep_the_same_private_owner(tmp_path, monkeypatch):
    from genshin_app import calculation_integrity
    from genshin_app.equipment import resource_reaction_receipt

    database = tmp_path / 'absent.db'
    plan = reaction_plan()
    plan['rotation_model']['damage_report'] = {
        'contract': 'damage-report-window-v1', 'mode': 'explicit',
        'end': plan['rotation_model']['period'],
    }
    complete = run_plan_calc(authorize(plan, database), database)
    value = {key: complete[key] for key in ('result', 'result_receipt')}
    original = calculation_integrity.validate_result_binding
    original_resource = resource_reaction_receipt.validate_resource_reaction_result_receipt
    owners = []

    def binding(*args, **kwargs):
        owners.append(('binding', kwargs['_session']))
        return original(*args, **kwargs)

    def resource(*args, **kwargs):
        owners.append(('resource', kwargs['_session']))
        return original_resource(*args, **kwargs)

    monkeypatch.setattr(calculation_integrity, 'validate_result_binding', binding)
    monkeypatch.setattr(resource_reaction_receipt, 'validate_resource_reaction_result_receipt', resource)
    calls = count_engine(monkeypatch)
    batch.review_calculation_batch(value, {'requests': [{'view': 'hits'}] * 2})
    assert [name for name, _ in owners] == ['binding', 'resource']
    session = owners[0][1]
    assert session is owners[1][1]
    assert len(calls) == session._usage['executions'] == 1
    assert session._usage['cache_hits'] >= 1
    assert session._closed and not session._cache


def test_batch_shared_verification_budget_failure_keeps_typed_boundary(tmp_path, monkeypatch):
    from genshin_app.equipment.execution_verification import verification_diagnostic

    complete = live_case(tmp_path)
    value = {key: complete[key] for key in ('result', 'result_receipt')}
    owner = ExecutionVerificationSession(budget={
        'policy': 'execution-verification-budget-v2', 'limits': {'requests': 0},
    })
    calls = count_engine(monkeypatch)
    with monkeypatch.context() as scoped:
        scoped.setattr(batch, 'ExecutionVerificationSession', lambda: owner)
        with pytest.raises(PlanError) as caught:
            batch.review_calculation_batch(value, {'requests': [{'view': 'resource_reactions'}]})
    diagnostic = verification_diagnostic(caught.value)
    assert diagnostic['issues'][0]['code'] == 'verification_budget'
    assert diagnostic['issues'][0]['dimension'] == 'requests'
    assert diagnostic['issues'][0]['attempted'] == 1
    assert diagnostic['budget']['effective']['requests'] == 0
    assert owner._closed and not owner._cache and not calls
    # A distinct operation gets a new private budget and recomputes normally;
    # it cannot inherit a stopped Owner or a previous request's approval/cache.
    assert batch.review_calculation_batch(value, {'requests': [{'view': 'resource_reactions'}]})['pages']
    assert len(calls) == 1


def test_standalone_result_transport_owns_isolated_scopes_and_rejects_fake_session(tmp_path, monkeypatch):
    from genshin_app.calculation_integrity import result_transport_parts
    from genshin_app.equipment.execution_verification import ExecutionVerificationError

    complete = live_case(tmp_path)
    value = {key: complete[key] for key in ('result', 'result_receipt')}
    original_init, owners = ExecutionVerificationSession.__init__, []

    def init(self, **kwargs):
        original_init(self, **kwargs)
        owners.append(self)

    monkeypatch.setattr(ExecutionVerificationSession, '__init__', init)
    assert result_transport_parts(value) == result_transport_parts(value)
    assert len(owners) == 2 and owners[0] is not owners[1]
    assert all(owner._closed and not owner._cache for owner in owners)
    with pytest.raises(ExecutionVerificationError) as caught:
        result_transport_parts(value, _session={'verified': True})
    assert caught.value.code == 'verification_scope'
    assert len(owners) == 2


def test_complete_agent_endpoint_validates_full_package_once(tmp_path, monkeypatch):
    db = tmp_path / 'missing.db'
    value = run_plan_calc(authorize(reaction_plan(), db), db, capture_display=True)
    from genshin_app.equipment import delivery
    original = delivery.validate_delivery_calculated
    calls, full = count_engine(monkeypatch), []

    def checked(*args, **kwargs):
        full.append(kwargs['_session'])
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery, 'validate_delivery_calculated', checked)
    monkeypatch.setattr(single, '_unpack', lambda *a, **k: pytest.fail('full calculated demoted to saved result'))
    output = batch._review_complete_calculated(value, {'requests': [{'view': 'hits'}] * 3})
    assert len(full) == len(calls) == 1
    assert all(p['review']['validation'] == 'full_calculation_package' for p in output['pages'])
    assert output['pages'][0]['review']['items'][0]['json_pointer'].startswith('/result/')


@pytest.mark.parametrize('part', ['plan', 'report', 'equipment_summary', 'display_context'])
def test_complete_calculated_binding_tamper_not_demoted(tmp_path, part):
    value = run_plan_calc(delivery_plan(), tmp_path / 'missing.db', capture_display=True)
    if part == 'plan':
        value[part]['manual_template']['loop_duration'] += 1
    elif part == 'report':
        value[part]['state'] = 'pending'
    elif part == 'equipment_summary':
        value[part]['forged'] = True
    else:
        value[part]['catalog']['forged'] = True
    with pytest.raises((PlanError, ValueError)):
        batch._review_complete_calculated(value, {'requests': [{}]})


def test_full_package_public_endpoint_exact_prefix(tmp_path):
    value = run_plan_calc(delivery_plan(), tmp_path / 'missing.db', capture_display=True)
    checked = validate_delivery_calculated(value)
    package = {'package_schema': PACKAGE_SCHEMA, 'kind': PACKAGE_KIND,
               'calculated': value, 'integrity': checked['integrity']}
    query = {'view': 'hits'}
    page = batch.review_calculation_batch(package, {'requests': [query]})['pages'][0]['review']
    assert page == _legacy_with_scope(package, query)
    assert page['items'][0]['json_pointer'].startswith('/calculated/result/')
    package['integrity']['report_fingerprint'] = '0' * 64
    with pytest.raises(PlanError):
        batch.review_calculation_batch(package, {'requests': [query]})


def test_source_tamper_after_success_still_rejected(tmp_path):
    value = live_case(tmp_path)
    request = {'requests': [{'view': 'resource_reactions'}]}
    batch.review_calculation_batch(value, request)
    value['result']['resource_reaction_source_adoption']['operations'][0]['source_owner'] = 'forged'
    resign(value)
    with pytest.raises(ValueError):
        batch.review_calculation_batch(value, request)


@pytest.mark.parametrize('query_request', [None, [], {}, {'requests': []}, {'requests': [{}] * 17},
    {'requests': [None]}, {'requests': [{}], 'verified': True}, {'requests': [{'limit': True}]},
    {'requests': [{'offset': True}]}, {'requests': [{'limit': 201}]},
    {'requests': [{'view': []}]}, {'requests': [{'filters': {'start': float('nan')}}]},
    {'requests': [{'filters': {'end': float('inf')}}]}, {'requests': [{'filters': {'character': '\ud800'}}]},
    {'requests': [{'unknown': 'x'}]}, {'requests': [{'filters': {'character': 'x' * 70000}}]}])
def test_bad_request_rejected_before_data(query_request, monkeypatch):
    monkeypatch.setattr(single, '_unpack', lambda *a, **k: pytest.fail('invalid request reached input'))
    with pytest.raises((PlanError, TypeError)):
        batch.review_calculation_batch(object(), query_request)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), '\udfff', {1: 'bad'}, object(), (1, 2)])
def test_invalid_plain_json(value):
    with pytest.raises(PlanError):
        bounded.plain_json(value, bounded.Budget(1000, 'test'))


def test_custom_container_protocols_never_called():
    class Trap(dict):
        def items(self):
            pytest.fail('custom items called')

        def __iter__(self):
            pytest.fail('custom iterator called')

    with pytest.raises(PlanError):
        batch.review_calculation_batch(Trap(_saved()), {'requests': [{}]})
    with pytest.raises(PlanError):
        batch.review_calculation_batch(_saved(), Trap(requests=[{}]))


def test_aliases_repeated_and_cycles_depth_nodes_budget():
    child = {'中文': ['\n', '🌟', 'é']}
    original = [child, child]
    budget = bounded.Budget(10000, 'test')
    copied, size = bounded.plain_json(original, budget)
    assert size == len(json_bytes(original)) and copied == original and copied[0] is not copied[1]
    one = bounded.Budget(10000, 'test')
    bounded.plain_json(child, one)
    assert budget.used > 2 * one.used
    cycle = []
    cycle.append(cycle)
    with pytest.raises(PlanError):
        bounded.plain_json(cycle, bounded.Budget(10000, 'test'))
    deep = 1
    for _ in range(65):
        deep = [deep]
    with pytest.raises(PlanError):
        bounded.plain_json(deep, bounded.Budget(10000, 'test'))
    with pytest.raises(PlanError):
        bounded.plain_json([1, 2], bounded.Budget(10000, 'test'), nodes=2)
    assert bounded.plain_json([1, 2], bounded.Budget(10000, 'test'), nodes=3)[0] == [1, 2]
    depleted = bounded.Budget(1, 'test')
    with pytest.raises(PlanError):
        bounded.plain_json('x', depleted)
    with pytest.raises(PlanError, match='结束'):
        depleted.charge(0)


def test_request_aggregate_is_limited_before_materials():
    page = {'view': 'attributes', 'filters': {name: 'a' * 512 for name in
            ('character', 'skill', 'damage_type_id', 'event_id', 'action_id', 'source_id',
             'stat', 'basis', 'receipt_id', 'frame_id')}}
    assert len(json_bytes(page)) < single.MAX_REQUEST_BYTES
    assert batch.review_calculation_batch(_saved(), {'requests': [page] * 12})['pages']
    with pytest.raises(PlanError) as failure:
        batch.review_calculation_batch(object(), {'requests': [page] * 13})
    assert failure.value.reason == 'review_size_limit'


def test_integer_conversion_work_reserved_before_str_or_encoding():
    huge = 1 << 1_000_000
    budget = bounded.Budget(1000, 'test')
    with pytest.raises(PlanError) as failure:
        bounded.plain_json(huge, budget)
    assert failure.value.reason == 'review_work_limit'
    assert budget.stopped
    # No undocumented 4096-bit acceptance cut-off in the new JSON container.
    assert bounded.plain_json(1 << 4097, bounded.Budget(10000, 'test'))[0] == 1 << 4097


def test_response_reservation_is_internal_one_use_and_closed():
    value = {'text': '中' * 200}
    budget = bounded.Budget(10000, 'test')
    size = bounded.prepay(value, budget)
    budget._encoding_input = (value, size)
    before = budget.used
    expected = bounded.encode(value, budget, maximum=10000)
    first_cost = budget.used - before
    before = budget.used
    assert bounded.encode(value, budget, maximum=10000) == expected
    assert budget.used - before > first_cost
    budget.close()
    assert budget._encoding_input is None
    with pytest.raises(PlanError):
        bounded.encode(value, budget, maximum=10000)


def test_embedded_wire_exact_canonical_and_size_check():
    budget = bounded.Budget(10000, 'test')
    body = bounded.encode({'items': ['中', 1]}, budget, maximum=10000)
    expected = json_bytes({'before': 1, 'projection': {'items': ['中', 1]}, 'zz': 2})
    assert bounded.embed_json({'before': 1, 'zz': 2}, 'projection', body, budget,
                              maximum=len(expected)) == expected
    with pytest.raises(PlanError):
        bounded.embed_json({'before': 1, 'zz': 2}, 'projection', body, budget, maximum=len(expected) - 1)


@pytest.mark.parametrize('value', [{'中': '\\\n\t\u0000🌟é', 'x': [1, -2, True, None, 1.25, {}, []]}, '', {}, [], 10**200])
@pytest.mark.parametrize('pretty,compact', [(False, True), (False, False), (True, False)])
def test_exact_encoding_size_boundary(value, pretty, compact):
    kwargs = {'ensure_ascii': False, 'sort_keys': compact, 'allow_nan': False}
    if pretty:
        kwargs['indent'] = 2
    elif compact:
        kwargs['separators'] = (',', ':')
    expected = json.dumps(value, **kwargs).encode('utf-8')
    assert bounded.encode(value, bounded.Budget(100000, 'test'), maximum=len(expected),
                          pretty=pretty, compact=compact) == expected
    with pytest.raises(PlanError):
        bounded.encode(value, bounded.Budget(100000, 'test'), maximum=len(expected) - 1,
                       pretty=pretty, compact=compact)


def test_snapshot_is_detached_before_verification_and_projection(monkeypatch):
    value = _saved()
    request = {'requests': [{'view': 'hits'}] * 2}
    expected = _legacy_with_scope(value, {'view': 'hits'})
    original = single._unpack

    def mutate(copied, **kwargs):
        assert copied is not value and copied['result'] is not value['result']
        value['result']['hits'][0]['expected'] = -999
        request['requests'][0]['view'] = 'summary'
        return original(copied, **kwargs)

    monkeypatch.setattr(single, '_unpack', mutate)
    actual = batch.review_calculation_batch(value, request)
    assert [p['review'] for p in actual['pages']] == [expected, expected]
    actual['pages'][0]['review']['items'][0]['expected'] = 5
    assert value['result']['hits'][0]['expected'] == -999


def test_budgets_same_thread_close_and_projection_accumulates(monkeypatch):
    original, captured = bounded.Budget.__init__, []

    def init(self, *a, **k):
        original(self, *a, **k)
        captured.append(self)

    monkeypatch.setattr(bounded.Budget, '__init__', init)
    batch.review_calculation_batch(_saved(), {'requests': [{'view': 'hits', 'limit': 1}]})
    once = next(b.used for b in captured if b.role == 'projection')
    assert all(b.closed for b in captured)
    monkeypatch.setattr(batch, 'PROJECTION_WORK', once + 1)
    with pytest.raises(PlanError, match='预算'):
        batch.review_calculation_batch(_saved(), {'requests': [{'view': 'hits', 'limit': 1}] * 16})
    assert all(b.closed for b in captured)
    budget, errors = bounded.Budget(100, 'test'), []

    def foreign():
        try:
            budget.charge(1)
        except PlanError as exc:
            errors.append(exc.reason)

    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join()
    assert errors == ['review_batch_scope']


def test_second_projection_failure_closes_without_partial_return(monkeypatch):
    value = _saved()
    value['result']['sampling_trace'] = ['invalid row']
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    with pytest.raises(PlanError):
        batch.review_calculation_batch(value, {'requests': [{'view': 'hits'}, {'view': 'sampling'}]})


def test_per_page_and_total_response_budget(monkeypatch):
    value = _saved()
    query = {'view': 'hits'}
    page = batch.review_calculation_batch(value, {'requests': [query]})
    monkeypatch.setattr(batch, 'BATCH_BYTES', len(json_bytes(page)) - 1)
    with pytest.raises(PlanError):
        batch.review_calculation_batch(value, {'requests': [query]})
    monkeypatch.setattr(batch, 'BATCH_BYTES', 2 * 1024 * 1024)
    monkeypatch.setattr(single, 'MAX_RESPONSE_BYTES', 100)
    with pytest.raises(PlanError):
        batch.review_calculation_batch(value, {'requests': [query]})


def test_private_finish_has_same_projection_budget_and_failure_closes(monkeypatch, tmp_path):
    value = run_plan_calc(delivery_plan(), tmp_path / 'missing.db', capture_display=True)
    captured = []

    def finish(output, budget):
        assert budget.used > 0 and not budget.closed
        captured.append(budget)
        raise OSError('publish failed')

    with pytest.raises(OSError):
        batch._review_complete_calculated(value, {'requests': [{}]}, _finish=finish)
    assert captured[0].closed
