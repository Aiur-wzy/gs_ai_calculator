"""Inspect saved successful or failed calculations without inventing a result."""
from __future__ import annotations

from genshin_app.calculation_review import MAX_FILE_BYTES, _unpack
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_engine.diagnostics import (
    MAX_RESPONSE_BYTES,
    DiagnosticError,
    DiagnosticIndex,
    diagnostics_contract,
)


def calculation_diagnostics_contract():
    from genshin_app.calculation_assistance import assistance_contract
    from genshin_app.energy_trials import energy_trials_contract
    return {**diagnostics_contract(), 'command': 'equipment diagnose',
            'input': 'calculated.json或带回执结果；也接受mechanics_pending错误、未封口核验容量/产物校验诊断及原引擎执行输出，即使未产生伤害。',
            'evidence': '有回执计算核对内部指纹；原执行或错误仅检查结构并绑定文件内容，不认证执行真实性。',
            'request': {'view': 'groups或issues，默认groups', 'filters': diagnostics_contract()['filters'],
                        'limit': '1..200，默认50', 'cursor': '原样使用上页next_cursor；更改输入或筛选须重新开始'},
            'auxiliary_views': {
                'assistance': {'request': '{view:"assistance",section?:"all"|"energy"|"coverage",limit?,offset?,expected?,observations?}',
                               'contract': assistance_contract()},
                'energy_trials': {'request': '{view:"energy_trials",trials:[...],include_baseline?}',
                                  'contract': energy_trials_contract()},
                'input_trials': {'request': '{view:"input_trials",trials:[{id,operations:[...]}],observations?}',
                                 'contract': energy_trials_contract()}},
            'auxiliary_authority': 'advisory_only; no candidate or acceptance effect'}


def inspect_calculation_diagnostics(value, request=None):
    request = {} if request is None else request
    try:
        value_bytes, request_bytes = len(json_bytes(value)), len(json_bytes(request))
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlanError('诊断输入与请求须为有限JSON', 'invalid_diagnostic_input') from exc
    if not isinstance(value, dict) or value_bytes > MAX_FILE_BYTES:
        raise PlanError('诊断输入不是对象或超过字节上限', 'invalid_diagnostic_input')
    if not isinstance(request, dict) or set(request)-{'view', 'filters', 'limit', 'cursor'} or request_bytes > 8192:
        raise PlanError('诊断请求含未知字段或过长', 'invalid_diagnostic_query')
    input_fp = fingerprint(value)
    from genshin_app.agent_chat.calculation_attempt_failure import (
        output_validation_failure_view,
    )
    from genshin_app.calculation_verification_failure import verification_failure_view
    verification = verification_failure_view(value)
    failure_view = verification if verification is not None else output_validation_failure_view(value)
    if failure_view is not None:
        execution, pointer, validation = failure_view
    elif value.get('code') == 1:
        if 'calculated' in value or 'result_receipt' in value:
            raise PlanError('诊断错误与带回执结果不能混合为同一容器', 'invalid_diagnostic_input')
        if value.get('reason') != 'mechanics_pending' or not isinstance(value.get('details'), dict):
            raise PlanError('当前仅支持机制执行未完成的结构化错误', 'invalid_diagnostic_input')
        execution, pointer = value['details'], '/details/issues'
        validation = 'unverified_saved_execution_error'
    elif 'calculated' in value or 'result_receipt' in value:
        result, _, validation = _unpack(value)
        execution = {'issues': result.get('mechanics_issues', []),
                     'status': result.get('mechanics_status', result.get('rotation_check', {}).get('status', 'not_checked')),
                     'mechanics_trace': result.get('mechanics_trace', [])}
        pointer = ('/calculated' if 'calculated' in value else '')+'/result/mechanics_issues'
    else:
        execution, pointer, validation = value, '/issues', 'unverified_saved_execution'
    if (not isinstance(execution.get('issues'), list) or not isinstance(execution.get('status'), str)
            or not isinstance(execution.get('mechanics_trace'), list)):
        raise PlanError('输入缺少机制执行状态、issues或mechanics_trace', 'invalid_diagnostic_input')
    cursor = request.get('cursor')
    if cursor is not None:
        if not isinstance(cursor, dict) or set(cursor) != {'input_fingerprint', 'page'} or cursor['input_fingerprint'] != input_fp:
            raise PlanError('诊断输入已变化，旧游标不能继续读取', 'stale_diagnostic_cursor')
        cursor = cursor['page']
    try:
        page = DiagnosticIndex(execution['issues'], status=execution['status'], pointer=pointer).page(
            **{k:v for k,v in request.items() if k != 'cursor'}, cursor=cursor)
    except DiagnosticError as exc:
        raise PlanError(str(exc), exc.code) from exc
    if page['next_cursor'] is not None:
        page['next_cursor'] = {'input_fingerprint': input_fp, 'page': page['next_cursor']}
    page.update(input_fingerprint=input_fp, validation_scope=validation,
                result_interpretation='仅定位已记录的问题；未完成执行不因诊断可读而取得伤害或DPS。')
    if len(json_bytes(page)) > MAX_RESPONSE_BYTES:
        raise PlanError('诊断页含输入回执后超限，请缩小limit', 'diagnostic_response_limit')
    return page
