"""One detached snapshot, one private verification session, bounded projections."""
from __future__ import annotations

from genshin_app import calculation_review as single
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.review_batch_json import (
    BATCH_BYTES,
    PROJECTION_WORK,
    REQUEST_BYTES,
    SNAPSHOT_WORK,
    Budget,
    invalid,
    plain_json,
    prepay,
)

CONTRACT = 'calculation-review-batch-v1'


def review_batch_contract():
    from genshin_app.review_batch_json import DEPTH, SNAPSHOT_BYTES, SNAPSHOT_NODES
    return {'contract': CONTRACT, 'command': 'equipment review-batch',
            'request': {'requests': '1..16 original calculation-review-v1 page requests; order and duplicates preserved'},
            'limits': {'request_bytes': REQUEST_BYTES, 'snapshot_bytes': SNAPSHOT_BYTES,
                       'snapshot_nodes': SNAPSHOT_NODES, 'depth': DEPTH,
                       'snapshot_work': SNAPSHOT_WORK, 'projection_work': PROJECTION_WORK,
                       'response_bytes': BATCH_BYTES, 'page_bytes': single.MAX_RESPONSE_BYTES},
            'scope': 'One detached historical snapshot and private verification session per call. '
                     'No approval reuse across calls or mutable inputs. Work/byte limits are not physical-memory guarantees.',
            'agent': 'gs_review_result_batch; use gs_read_context for historical projection resources. '
                     'Every subtree retains captured identity; JSON pointers never resolve against a current candidate.'}


def _requests(request, budget):
    request, _ = plain_json(request, budget, maximum=REQUEST_BYTES)
    if type(request) is not dict or set(request) != {'requests'}:
        invalid('批量请求必须恰含requests字段')
    pages = request['requests']
    if type(pages) is not list or not 1 <= len(pages) <= 16:
        invalid('批量请求须包含1至16个单页请求')
    parsed = []
    for page in pages:
        if type(page) is not dict:
            invalid('每个单页请求须为对象')
        prepay(page, budget, maximum=single.MAX_REQUEST_BYTES)
        parsed.append(single._request(page))
    return parsed


def review_calculation_batch(value, request):
    """Public saved-result/package endpoint; no supplied approval/session state."""
    return _run(value, request, complete=False)


def _review_complete_calculated(value, request, *, _finish=None):
    """Internal Agent endpoint: always validates every full calculated binding."""
    return _run(value, request, complete=True, finish=_finish)


def _review_calculation_file(path, request, *, finish):
    from genshin_app.review_batch_json import SNAPSHOT_BYTES, read_json
    return _run(None, request, complete=False,
                load=lambda: read_json(path, SNAPSHOT_BYTES), finish=finish)


def _run(value, request, *, complete, load=None, finish=None):
    snapshot_budget = Budget(SNAPSHOT_WORK, 'snapshot')
    projection_budget = Budget(PROJECTION_WORK, 'projection')
    try:
        parsed = _requests(request, snapshot_budget)
        if load is not None:
            value = load()
        snapshot, _ = plain_json(value, snapshot_budget)
        with ExecutionVerificationSession() as session:
            if complete:
                from genshin_app.equipment.delivery import validate_delivery_calculated
                validate_delivery_calculated(snapshot, _session=session)
                result, receipt = snapshot['result'], snapshot['result_receipt']
                single._check_bound_result(result, receipt, _session=session)
                validation, prefix = 'full_calculation_package', ''
            else:
                result, receipt, validation = single._unpack(snapshot, _session=session)
                prefix = '/calculated' if 'calculated' in snapshot else ''
            prepay(result.get('action_event_compilations', []), projection_budget)
            from genshin_app.review_reaction_projection import ReviewLineage
            lineage = ReviewLineage(single._lineage(result))
            identity = {key: receipt.get(key) for key in
                        ('plan_id', 'revision', 'input_fingerprint', 'source_fingerprint', 'result_fingerprint')}
            output = {'contract': CONTRACT, 'historical_projection': True,
                      'identity': identity,
                      'pointer_scope': 'json_pointer refers only to the original captured calculation document; never the current candidate',
                      'scope': '历史内部一致性与所选字段投影；不认证游戏完整、当前数据库或当前候选，不授予来源批准。',
                      'pages': []}
            used = prepay(output, projection_budget, maximum=BATCH_BYTES)
            for index, query in enumerate(parsed):
                # Reserve entry punctuation and page index before page allocation.
                remaining = BATCH_BYTES - used - 32 - (1 if index else 0)
                page = single._project_review(result, receipt, validation, prefix, query, lineage, projection_budget,
                                              response_maximum=min(single.MAX_RESPONSE_BYTES, remaining))
                entry = {'page_index': index, 'review': page}
                size = prepay(entry, projection_budget, maximum=BATCH_BYTES)
                if used + size + (1 if index else 0) > BATCH_BYTES:
                    from genshin_app.equipment.schema import PlanError
                    raise PlanError('批量响应超过2MiB，请减少页数、limit或增加过滤', 'review_size_limit')
                used += size + (1 if index else 0)
                output['pages'].append(entry)
            size = prepay(output, projection_budget, maximum=BATCH_BYTES)
            projection_budget._encoding_input = (output, size)
            return finish(output, projection_budget) if finish is not None else output
    finally:
        snapshot_budget.close()
        projection_budget.close()
