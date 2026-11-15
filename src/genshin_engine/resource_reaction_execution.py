"""Read-only, bidirectional RQ1 occurrence/receipt consistency.

No selector, resource operation, damage expression or database approval executes
here. The runtime supplies its one cumulative budget; external callers must
first apply equivalent bounded input validation.
"""
from __future__ import annotations

from decimal import Decimal

from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET
from genshin_engine.resource_reaction_program import (
    REACTION_TYPES,
    ReactionResourceError,
    reaction_members,
    valid_v4_reaction_member,
)
from genshin_engine.shared_pools import pool_capacity_limit, pool_capacity_mode


def fail(message):
    raise ReactionResourceError('resource_reaction_verification', message, severity='pending')


def ticks(value):
    result = Decimal(str(value)) * 1000000
    if result != result.to_integral_value():
        fail('实际发生时刻不是完整微秒。')
    return int(result)


def unique(rows, field):
    result = {}
    for row in rows:
        key = row[field]
        if key in result:
            fail('同一实际发生身份重复。')
        result[key] = row
    return result


def generated_applications(mechanics_trace):
    requests = {}
    for row in mechanics_trace:
        if row['type'] != 'special_reaction_application_request':
            continue
        run_id = f"{row['cycle']}:{row['event_id']}"
        if run_id in requests:
            fail('派生附着请求重复使用同一实际发生身份。')
        requests[run_id] = row
    return requests


def verify_generated_application(application, request):
    if (request['status'] != 'ready_for_elemental_runtime'
            or application.get('generated_application_request_id') != request['event_id']
            or application.get('parent_damage_event_id') != request.get('parent_damage_event_id')
            or application.get('application_order') != request['application_order']
            or application['character'] != request['owner'] or application['element'] != request['element']
            or application['action_id'] != request['action_id'] or application['area_id'] != request['area_id']
            or application['direct_hit'] is not False or ticks(application['t']) != request['time_tick']
            or (request.get('target_id') is not None and application['target_id'] != request['target_id'])):
        fail('实际派生附着与原始排队请求不一致。')


def verify(program, occurrences, operations, event_log, mechanics_trace, elemental_trace, resource_trace, digest,
           *, runtime_events, raw_resource_trace=None):
    """All arguments are borrowed read-only; no complete output gets recopied."""
    operation_defs = {row['id']: row for row in program['operations']}
    ruleset = program.get('elemental_ruleset', DYNAMIC_SWIRL_RULESET)
    multi = ruleset == MULTI_AURA_RULESET
    if raw_resource_trace is not None:
        if len(resource_trace) != len(raw_resource_trace):
            fail('运行时资源轨迹与实际资源账长度不同。')
        for row, raw in zip(resource_trace, raw_resource_trace, strict=True):
            expected = {**raw, 'qualification_scope': 'engine_declared_event'}
            if any(key not in row for key in expected) or digest(expected) != digest({key: row[key] for key in expected}):
                fail('运行时资源轨迹与实际资源账原始回执不同。')
    parents = unique((r for r in event_log if r['type'] == 'mechanics_hit'
                      or (multi and r['type'] == 'elemental_application')), 'run_id')
    # This is the already materialized native hit/pattern directory, not a
    # directory inferred from the very application traces under verification.
    # These elemental modes require an application descriptor on every native hit.
    hit_definitions = unique((r for r in runtime_events if r['kind'] == 'mechanics_hit'), 'id')
    apps = unique((r for r in elemental_trace if r['type'] == 'elemental_application'), 'run_id')
    app_copies = unique((r for r in mechanics_trace if r['type'] == 'elemental_application'), 'run_id')
    reactions = unique((r for r in mechanics_trace if r['type'] == 'elemental_reaction'), 'run_id')
    generated = generated_applications(mechanics_trace) if multi else {}
    for run, parent in parents.items():
        if parent['type'] == 'mechanics_hit':
            definition = hit_definitions.get(parent['id'])
            if definition is None or parent['action_id'] != definition['data']['action_id']:
                fail('实际命中没有最终装配图中的唯一父定义。')
        elif run not in generated:
            fail('无伤害附着没有唯一派生请求。')
        if parent['status'] in ('passed', 'missed') and run not in apps:
            fail('已完成或未命中的父事件缺少实际附着尝试；零反应也必须保留尝试。')
    actual, auditable = {}, set()
    for run, application in apps.items():
        if run not in app_copies or digest(application) != digest(app_copies[run]):
            fail('元素账与机制账中的实际附着不一致或缺失。')
        parent = parents.get(run)
        if parent is None or parent['status'] not in ('passed', 'missed', 'pending'):
            fail('实际附着没有已到执行范围的父事件。')
        if (application.get('ruleset') != ruleset
                or application['stable_event_id'] != parent['id']
                or application['event_id'] != run or application['action_id'] != parent['action_id']
                or application['cycle'] != parent['source_cycle'] or ticks(application['t']) != ticks(parent['t'])):
            fail('实际附着尝试与父事件身份或时刻不一致。')
        hit_parent = parent['type'] == 'mechanics_hit'
        if hit_parent:
            data = hit_definitions[parent['id']]['data']
            declared = data['descriptor']['application']
            if (application['character'] != data['hit']['character'] or application['element'] != data['hit']['element']
                    or application['target_id'] != declared['target_id'] or application['profile_id'] != declared['profile_id']):
                fail('实际附着尝试与最终父定义的角色、元素、目标或档案不一致。')
        else:
            verify_generated_application(application, generated[run])
        for index, reaction in enumerate(reaction_members(application, ruleset)):
            if (application.get('allowed') is not True or application.get('outcome') != 'reaction'
                    or reaction['parent_run_id'] != run or reaction['parent_event_id'] != parent['id']
                    or reaction['owner'] != application['character'] or reaction['target_id'] != application['target_id']
                    or reaction['action_id'] != parent['action_id']):
                fail('原始附着的嵌套反应未绑定唯一真实命中。')
            eligible = hit_parent and (not multi or reaction['type'] in REACTION_TYPES)
            if multi and not valid_v4_reaction_member(application, reaction, index):
                fail('多元素反应与已提交分支顺序或元素不一致。')
            uid = reaction['run_id']
            if uid in actual:
                fail('两个原始附着复用了同一实际反应身份。')
            actual[uid] = (application, reaction, parent)
            if eligible:
                auditable.add(uid)
            expected = {**reaction, 'type': 'elemental_reaction', 'reaction_type': reaction['type'],
                'event_id': reaction['id'], 'cycle': parent['source_cycle'], 't': application['t'], 'status': 'executed'}
            callback = reactions.get(uid)
            if callback is None:
                if parent['status'] == 'passed':
                    fail('已完成父事件的实际反应缺少回调记录。')
            elif digest(callback) != digest(expected):
                fail('实际反应回调与原始附着中的嵌套反应不一致。')
    if set(app_copies) != set(apps) or not set(reactions) <= set(actual):
        fail('实际反应或附着记录没有唯一原始发生来源。')
    if multi and list(reactions) != [uid for uid in actual if uid in reactions]:
        fail('多元素实际反应回调顺序与原始附着不一致。')
    scopes = unique(occurrences, 'reaction_run_id')
    if multi and list(scopes) != [uid for uid in reactions if uid in scopes]:
        fail('多元素反应审计顺序与实际回调不一致。')
    by_id = unique(occurrences, 'id')
    rows_by_scope = {identity: {} for identity in by_id}
    row_ids, span_used = set(), set()
    for scope in occurrences:
        linked = actual.get(scope['reaction_run_id'])
        if linked is None or scope['reaction_run_id'] not in reactions or scope['reaction_run_id'] not in auditable:
            fail('审计发生没有真实反应回调。')
        application, reaction, parent = linked
        material = {'id': parent['id'], 'run_id': parent['run_id'], 'origin': parent['source_cycle'],
            'time_us': ticks(parent['t']), 'order': parent['order'], 'kind': parent['type'],
            'action_id': parent['action_id'], 'frontline': parent['frontline']}
        expected = {'parent_event_id': parent['id'], 'parent_run_id': parent['run_id'],
            'reaction_event_id': reaction['id'], 'reaction_run_id': reaction['run_id'],
            'origin': parent['source_cycle'], 'time_us': ticks(parent['t']), 'target_id': reaction['target_id'],
            'owner': reaction['owner'], 'frontline': parent['frontline'], 'reaction_type': reaction['type'],
            'trigger_element': reaction['trigger_element'], 'aura_element': reaction['aura_element'],
            'application_fingerprint': digest(application), 'reaction_fingerprint': digest(reaction),
            'parent_fingerprint': digest(material)}
        if any(type(scope.get(k)) is not type(value) or scope.get(k) != value for k, value in expected.items()):
            fail('审计发生的身份、时刻或原始材料指纹不一致。')
        if parent['status'] == 'passed' and not (scope['resource_scan_complete'] and scope['callback_complete']):
            fail('已完成父事件却缺少完整反应资源扫描。')
    for uid in auditable:
        if actual[uid][2]['status'] == 'passed' and uid not in scopes:
            fail('已完成父事件的原始反应未进入审计；不能同时删回调与收入来免检。')
    for row in operations:
        scope = by_id.get(row['occurrence_id'])
        definition = operation_defs.get(row['operation_id'])
        if scope is None or definition is None or row['id'] in row_ids:
            fail('审计operation身份、来源或发生引用无效。')
        row_ids.add(row['id'])
        scope_rows = rows_by_scope[scope['id']]
        if row['operation_id'] in scope_rows:
            fail('同一反应operation被重复选择。')
        scope_rows[row['operation_id']] = row
        if (row['pool_id'] != definition['pool_id'] or row['source_id'] != definition['source_id']
                or row['source_owner'] != definition['source_owner'] or row['keys'] != definition['keys']):
            fail('审计资源对象与冻结实际图不一致。')
        state = row['state']
        parent_passed = actual[scope['reaction_run_id']][2]['status'] == 'passed'
        gate = next((g for g in program.get('lifecycle_gates', []) if g['operation_id'] == row['operation_id']), None)
        qualification = row.get('lifecycle_qualification')
        if gate and row['decision'] == 'match':
            if (program['contract'] != 'ordinary-reaction-resource-v2' or type(qualification) is not dict
                    or qualification.get('authority') != 'actual_completed_state2_current_generation'
                    or qualification.get('operation_id') != row['operation_id']
                    or qualification.get('pool_id') != row['pool_id']
                    or qualification.get('lifecycle_id') != gate['lifecycle_id']
                    or qualification.get('window') != gate['window']
                    or qualification.get('generation_policy') != gate['generation_policy']
                    or any(type(qualification.get(k)) is not type(scope[k]) or qualification[k] != scope[k]
                           for k in ('reaction_run_id', 'parent_run_id', 'origin', 'time_us'))
                    or type(qualification.get('qualified')) is not bool):
                fail('生命周期资格回执与实际选择和版本不一致。')
        elif qualification is not None:
            fail('无已声明资格门的资源不能携带生命周期授权。')
        if state == 'blocked_lifecycle':
            if (gate is None or row['decision'] != 'match' or qualification['qualified'] is not False
                    or row['ledger_begin'] is not None or row['actual_end'] is not None or row['expected_count'] != 0):
                fail('生命周期阻止收入的记录不能携带资源提交。')
            continue
        if gate and row['decision'] == 'match' and qualification['qualified'] is not True:
            fail('资源已选择但没有当前有效生命周期资格。')
        if state in ('no_match', 'unknown_condition'):
            if row['decision'] != state or row['ledger_begin'] is not None or row['actual_end'] is not None or row['expected_count'] != 0:
                fail('未选择operation不能声明资源提交。')
            continue
        if row['decision'] != 'match' or row['expected_count'] != len(definition['keys']):
            fail('已选择operation的计量目录不一致。')
        if state != 'committed':
            if parent_passed or state not in ('planned', 'failed', 'committed_inconsistent'):
                fail('完整发生中包含未提交或未知状态的资源槽。')
            if state == 'committed_inconsistent':
                fail('资源已提交但游标异常，不能宣称已核验。')
            continue
        start, end = row['ledger_begin'], row['actual_end']
        if (type(start) is not int or type(end) is not int or start < 0 or end > len(resource_trace)
                or end - start != len(definition['keys'])):
            fail('资源审计span越界或未覆盖完整keys。')
        keys = []
        for index in range(start, end):
            receipt = resource_trace[index]
            if index in span_used:
                fail('同一实际资源回执被多个审计span复用。')
            span_used.add(index)
            if (receipt['type'] != 'operation' or receipt['operation_id'] != row['operation_id']
                    or receipt['pool_id'] != row['pool_id'] or receipt['source_id'] != row['source_id']
                    or receipt['run_id'] != scope['reaction_run_id'] or receipt['event_id'] != scope['reaction_event_id']
                    or receipt['t_us'] != scope['time_us'] or receipt['cycle'] != scope['origin']
                    or receipt['phase'] != 'post' or receipt['operation'] != 'grant' or receipt['qualified'] is not True):
                fail('资源回执与实际反应operation不一致。')
            if receipt['id'] != 'pool:' + digest(['operation', row['operation_id'], scope['reaction_run_id'], receipt['key']]):
                fail('实际资源回执身份摘要不一致。')
            amount = definition['operation']['amount']
            cap = definition['pool']['capacity']
            mode = pool_capacity_mode(definition['pool'])
            limit = pool_capacity_limit(definition['pool'])
            for field in ('before', 'requested', 'accepted', 'after', 'delta', 'wasted', 'cooldown_until_us'):
                if type(receipt.get(field)) is not int:
                    fail('常量资源回执必须使用完整整数计量。')
            if (receipt['requested'] != amount or receipt.get('capacity_mode', 'bounded') != mode
                    or not 0 <= receipt['before'] <= limit or not 0 <= receipt['after'] <= limit
                    or receipt['accepted'] != receipt['after'] - receipt['before']
                    or receipt['delta'] != receipt['accepted'] or not 0 <= receipt['accepted'] <= amount):
                fail('实际资源回执的收入量与冻结常量或前后差不一致。')
            if receipt['reason'] == 'granted':
                expected = amount if mode == 'unbounded' else min(amount, cap-receipt['before'])
                if (receipt['accepted'] != expected or receipt['wasted'] != amount-receipt['accepted']
                        or receipt['succeeded'] is not True):
                    fail('实际grant回执的满池处理不一致。')
            elif receipt['reason'] == 'cooldown':
                if receipt['accepted'] != 0 or receipt['wasted'] != 0 or receipt['succeeded'] is not False or receipt['cooldown_until_us'] <= scope['time_us']:
                    fail('冷却拒绝回执不能记作真实收入。')
            else:
                fail('已选择grant的实际结果原因不在闭合范围。')
            if ('unit_scale' in definition['pool']
                    and (receipt.get('unit_scale') != definition['pool']['unit_scale'] or receipt.get('quantity_unit') != 'atoms')):
                fail('资源回执的计量单位不一致。')
            keys.append(receipt['key'])
        if keys != definition['keys']:
            fail('一个反应operation必须一次交付完整key集合。')
    for scope in occurrences:
        if scope['resource_scan_complete'] and set(rows_by_scope[scope['id']]) != set(operation_defs):
            fail('完整资源扫描遗漏match或no_match决策。')
    for index, receipt in enumerate(resource_trace):
        if receipt['type'] == 'operation' and receipt['operation_id'] in operation_defs and index not in span_used:
            fail('实际反应资源收入未被唯一已提交审计span覆盖。')
    return {'status': 'verified', 'scope': 'stored_occurrence_consistency_no_source_authority',
            'actual_reactions': len(actual), 'audit_occurrences': len(occurrences),
            'audited_operations': len(operations), 'committed_resource_receipts': len(span_used)}
