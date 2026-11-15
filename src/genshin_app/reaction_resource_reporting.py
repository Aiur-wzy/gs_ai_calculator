"""Pure readonly joins of actual reaction audits and original resource receipts.

This is presentation, not saved-proof validation or source approval.
"""
from __future__ import annotations

import json

CONTRACT = 'ordinary-reaction-resource-v1'
CONTRACTS = {CONTRACT, 'ordinary-reaction-resource-v2'}
STATES = {'no_match': '未匹配，未尝试收入', 'unknown': '触发条件待核',
          'planned': '已计划，尚未提交', 'failed': '操作失败',
          'committed_inconsistent': '已有提交，关联待核', 'committed': '已提交资源操作',
          'blocked_lifecycle': '领域资格不满足，未尝试收入'}
REACTIONS = {'melt': '融化', 'vaporize': '蒸发', 'swirl': '扩散'}
ELEMENTS = {'pyro': '火', 'hydro': '水', 'cryo': '冰', 'anemo': '风',
            'electro': '雷', 'dendro': '草', 'geo': '岩', 'physical': '物理'}
VERIFY = {'verified': '保存记录一致', 'pending': '待核验', 'failed': '核验失败'}
ADOPTION = {'ready': '已采用', 'excluded': '已排除', 'pending': '待确认', 'partial': '部分采用'}
WINDOWS = {'complete_reported_input': '本次完整声明窗口', 'executed_prefix': '已执行前缀'}


def _int(value):
    # Match the GUI's exact integer domain; oversized saved values stay unknown.
    return type(value) is int and 0 <= value <= 9_007_199_254_740_991


def _index(rows):
    result = {}
    for index, row in enumerate(rows):
        if isinstance(row, dict) and _int(row.get('id')):
            result.setdefault(row['id'], []).append((index, row))
    return result


def reaction_resource_window(result):
    mechanics = result.get('mechanics') or {}
    rotation = result.get('rotation_check') or {}
    interpretation = rotation.get('damage_interpretation') if isinstance(rotation, dict) else None
    if (isinstance(mechanics, dict) and mechanics.get('complete_first_cycle') is False
            or isinstance(interpretation, dict) and interpretation.get('kind') == 'executed_prefix'):
        return 'executed_prefix'
    audit = result.get('resource_reactions') or {}
    return audit.get('verified_window') if isinstance(audit, dict) else None


def lifecycle_qualification(result, audit, operation, occurrence):
    gates = [g for g in (audit.get('program') or {}).get('lifecycle_gates', [])
             if isinstance(g, dict) and g.get('operation_id') == operation.get('operation_id')]
    raw = operation.get('lifecycle_qualification')
    if not gates and raw is None:
        return None
    gate = gates[0] if len(gates) == 1 else {}
    lid = gate.get('lifecycle_id')
    models = (result.get('resource_reaction_execution') or {}).get('mechanics_model') or {}
    lives = [r for r in (models.get('state_runtime') or {}).get('lifecycles', [])
             if isinstance(r, dict) and r.get('id') == lid]
    life = lives[0] if len(lives) == 1 else {}
    row = {'lifecycle_id': lid, 'name_zh': life.get('name_zh') or lid or '领域未记录',
           'source_id': life.get('source_id'), 'source_owner': life.get('owner'),
           'qualified': None, 'reason': None, 'reason_label': '领域资格待核',
           'generation_uid': None, 'revision': None, 'established_us': None, 'end_us': None, 'issue': None}
    if len(gates) != 1:
        row['issue'] = '领域资格与合同关联不唯一'
        return row
    adoption = result.get('resource_reaction_source_adoption')
    adopted = adoption.get('lifecycle_gates') if isinstance(adoption, dict) else None
    bindings = [b for b in adopted if isinstance(b, dict) and b.get('gate') == gate] if isinstance(adopted, list) else []
    binding = bindings[0] if len(bindings) == 1 else {}
    if (len(lives) != 1 or len(bindings) != 1
            or adoption.get('contract') != 'ordinary-reaction-resource-adoption-v2'
            or not isinstance(life.get('source_id'), str) or not life['source_id']
            or not isinstance(life.get('owner'), str) or not life['owner']
            or binding.get('lifecycle_id') != lid or binding.get('source_id') != life['source_id']
            or binding.get('source_owner') != life['owner'] or life['owner'] != operation.get('source_owner')
            or binding.get('status') != 'ready' or binding.get('lifecycle_source_status') != 'ready'):
        row['issue'] = '领域定义、来源与采用记录关联不一致'
        return row
    if raw is None and operation.get('state') in ('no_match', 'unknown'):
        row['reason_label'] = '触发未匹配或待核，未检查领域'
        return row
    identity = {'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
                'lifecycle_id': lid, 'reaction_run_id': occurrence.get('reaction_run_id'),
                'parent_run_id': occurrence.get('parent_run_id'), 'time_us': occurrence.get('time_us'),
                'origin': occurrence.get('origin'),
                'window': gate.get('window'), 'generation_policy': gate.get('generation_policy'),
                'authority': 'actual_completed_state2_current_generation'}
    if (not isinstance(raw, dict) or type(raw.get('qualified')) is not bool
            or any(type(raw.get(k)) is not type(v) or raw.get(k) != v for k, v in identity.items())):
        row['issue'] = '领域资格与实际反应关联不一致'
        return row
    row.update({k: raw.get(k) for k in ('qualified', 'reason', 'generation_uid', 'revision', 'established_us', 'end_us')})
    labels = {'eligible': '领域生效，允许尝试收入', 'not_started': '领域尚未创建',
              'not_established': '领域尚未建立', 'ended': '领域已结束或不在有效窗口'}
    row['reason_label'] = labels.get(raw.get('reason'), '资格原因未分类，见原始记录')
    reason, phase = raw.get('reason'), raw.get('phase')
    t, created, start, end = (raw.get(k) for k in ('time_us', 'created_us', 'established_us', 'end_us'))
    if not _int(t) or not _int(raw.get('origin')):
        row['issue'] = '领域资格的反应时点或窗口身份无效'
    elif reason == 'not_started':
        if (raw['qualified'] or phase != 'not_started'
                or any(raw.get(k) is not None for k in ('generation_uid', 'revision', 'created_us', 'established_us', 'end_us'))):
            row['issue'] = '未创建领域不能带有已建立的状态'
    elif (reason not in ('eligible', 'not_established', 'ended')
            or phase not in ('created', 'established', 'closing', 'ended')
            or not isinstance(raw.get('generation_uid'), str) or not raw['generation_uid']
            or not _int(raw.get('revision')) or raw['revision'] < 1
            or not all(_int(v) for v in (created, start, end)) or not created <= start < end or t < created):
        row['issue'] = '领域资格的状态、代标识或时间窗无效'
    else:
        # Compare recorded fields only: do not run the lifecycle or resource engine.
        eligible = phase == 'established' and start <= t < end
        expected_reason = 'eligible' if eligible else 'not_established' if phase == 'created' and t < end else 'ended'
        if raw['qualified'] != eligible or reason != expected_reason:
            row['issue'] = '领域资格与已记录阶段、判定窗口矛盾'
    if ((operation.get('state') == 'blocked_lifecycle' and raw['qualified'])
            or operation.get('state') in ('committed', 'committed_inconsistent') and not raw['qualified']):
        row['issue'] = '领域资格与资源提交状态矛盾'
    return row


def reaction_resource_rows(result):
    audit = result.get('resource_reactions')
    if not isinstance(audit, dict) or audit.get('contract') not in CONTRACTS:
        return []
    occurrences = audit.get('occurrences', [])
    operations = audit.get('operations', [])
    trace = (result.get('shared_pools') or {}).get('trace', [])
    if not all(isinstance(value, list) for value in (occurrences, operations, trace)):
        return []
    by_occurrence = _index(occurrences)
    rows, claims = [], {}
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        ident = operation.get('occurrence_id')
        matching = by_occurrence.get(ident, []) if _int(ident) else []
        occurrence = matching[0][1] if len(matching) == 1 else {}
        problem = None if occurrence else ('反应记录缺失' if not matching else '反应记录不唯一')
        state = operation.get('state')
        row = {'path': f'/resource_reactions/operations/{position}',
               'occurrence_path': f'/resource_reactions/occurrences/{matching[0][0]}' if occurrence else None,
               'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
               'source_id': operation.get('source_id'), 'source_owner': operation.get('source_owner'),
               'reaction_owner': occurrence.get('owner'), 'frontline': occurrence.get('frontline'),
               'parent_event_id': occurrence.get('parent_event_id'), 'parent_run_id': occurrence.get('parent_run_id'),
               'reaction_event_id': occurrence.get('reaction_event_id'), 'reaction_run_id': occurrence.get('reaction_run_id'),
               'time_us': occurrence.get('time_us'), 'target_id': occurrence.get('target_id'),
               'reaction_label': REACTIONS.get(occurrence.get('reaction_type'), '反应种类未记录'),
               'trigger_element_label': ELEMENTS.get(occurrence.get('trigger_element'), '未记录'),
               'aura_element_label': ELEMENTS.get(occurrence.get('aura_element'), '未记录'),
               'decision': operation.get('decision'), 'state': state,
               'state_label': STATES.get(state, '操作状态未说明'), 'association_issue': problem,
               'failure_class': operation.get('failure_class'), 'receipts': [],
               'verification_status': audit.get('verification_status'), 'verified_window': audit.get('verified_window'),
               'display_execution_window': reaction_resource_window(result),
               'source_authority': audit.get('source_authority')}
        if audit['contract'] == 'ordinary-reaction-resource-v2':
            qualification = lifecycle_qualification(result, audit, operation, occurrence)
            row['lifecycle_qualification'] = qualification
            if qualification and qualification['issue']:
                row['association_issue'] = row['association_issue'] or qualification['issue']
        begin, end = operation.get('ledger_begin'), operation.get('actual_end')
        if state not in ('committed', 'committed_inconsistent'):
            # A planned/failed/no-match slot is never rendered as actual income.
            if end is not None:
                row['association_issue'] = '未提交状态与实际资源区间矛盾'
            rows.append(row)
            continue
        count, keys = operation.get('expected_count'), operation.get('keys')
        if (not _int(begin) or not _int(end) or not _int(count) or not isinstance(keys, list)
                or any(not isinstance(key, str) or not key for key in keys)
                or len(set(keys)) != len(keys)
                or end < begin or end > len(trace) or end - begin != count or count != len(keys)):
            row['association_issue'] = '资源流水区间不完整'
            rows.append(row)
            continue
        seen_keys = set()
        for ledger_index in range(begin, end):
            receipt = trace[ledger_index]
            if not isinstance(receipt, dict):
                row['association_issue'] = '资源流水格式不完整'
                break
            identity = {'type': 'operation', 'operation': 'grant', 'phase': 'post',
                        'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
                        'source_id': operation.get('source_id'), 'run_id': occurrence.get('reaction_run_id'),
                        'event_id': occurrence.get('reaction_event_id'), 't_us': occurrence.get('time_us')}
            if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                   for key, value in identity.items()) or receipt.get('key') not in keys:
                row['association_issue'] = '资源流水与反应操作不一致'
                break
            if receipt['key'] in seen_keys:
                row['association_issue'] = '资源键重复，区间未完整覆盖声明对象'
                break
            seen_keys.add(receipt['key'])
            item = {key: receipt.get(key) for key in ('id', 'key', 'before', 'requested', 'accepted',
                                                     'after', 'wasted', 'unit_scale', 'quantity_unit',
                                                     'reason', 'cooldown_until_us', 'succeeded')}
            item['path'] = f'/shared_pools/trace/{ledger_index}'
            if receipt.get('reason') == 'cooldown':
                item['label'] = '冷却拒绝'
            elif type(receipt.get('accepted')) in (int, float) and receipt['accepted'] > 0:
                item['label'] = '实际收入'
            elif receipt.get('accepted') == 0 and receipt.get('reason') == 'granted':
                item['label'] = '已尝试，实际收入为0'
            else:
                item['label'] = '资源尝试，结果见原账'
            row['receipts'].append(item)
            claims.setdefault(ledger_index, []).append(row)
        if row['association_issue']:
            row['receipts'] = []
        rows.append(row)
    for owners in claims.values():
        if len(owners) > 1:
            for row in owners:
                row['association_issue'] = '同一资源流水被重复引用'
                row['receipts'] = []
    return rows


def reaction_resource_sheet(result):
    """Workbook cells only; values stay in original units, with no new formula."""
    audit = result.get('resource_reactions')
    adoption = result.get('resource_reaction_source_adoption')
    if not isinstance(audit, dict) and not isinstance(adoption, dict):
        return None
    rows = [['反应资源联动', '只读实际记录；此页不另加资源或伤害。来源采用与执行一致性分开。'],
            ['来源说明', '调用方声明；保存一致性不认证当前数据库或游戏事实。'],
            ['发生核验', VERIFY.get((audit or {}).get('verification_status'), '待核验' if audit else '无执行记录')],
            ['核验窗口', WINDOWS.get(reaction_resource_window(result),
                                  '核验范围待明确，见原记录' if audit else '无执行记录')],
            ['来源采用', ADOPTION.get((adoption or {}).get('status'), '未明确')],
            ['秒数', '父事件', '反应执行', '反应', '反应角色', '资源来源角色', '来源', '资源池',
             '状态 / 关联', '资源键', '请求', '接受', '浪费', '之前 / 之后', '每单位原子数', '冷却至秒数', '原始位置']]
    for row in reaction_resource_rows(result):
        time = row['time_us']/1_000_000 if _int(row['time_us']) else '未记录'
        base = [time, row['parent_event_id'], row['reaction_run_id'], row['reaction_label'],
                row['reaction_owner'], row['source_owner'], row['source_id'], row['pool_id'],
                row['association_issue'] or row['state_label']]
        if not row['receipts']:
            rows.append(base + [None]*7 + [row['path']])
        for receipt in row['receipts']:
            end = receipt.get('cooldown_until_us')
            rows.append(base[:8] + [base[8]+' / '+receipt['label'], receipt['key'], receipt['requested'],
                        receipt['accepted'], receipt['wasted'], f"{receipt['before']} / {receipt['after']}",
                        receipt['unit_scale'], end/1_000_000 if _int(end) else None, receipt['path']])
    if (audit or {}).get('contract') == 'ordinary-reaction-resource-v2':
        rows.append(['领域资格（只读）', '按反应发生时的实际领域状态判断；窗口包含开始、不含结束；未来建立时间不表示已经生效。'])
        rows.append(['父事件', '领域', '领域来源', '来源角色', '资格', '本代标识', '修订', '建立时间秒', '结束时间秒', '原始位置'])
        for row in reaction_resource_rows(result):
            q = row.get('lifecycle_qualification')
            if q:
                rows.append([row['parent_event_id'], q['name_zh'], q['source_id'], q['source_owner'],
                             q['issue'] or q['reason_label'], q['generation_uid'], q['revision'],
                             q['established_us']/1_000_000 if _int(q['established_us']) else None,
                             q['end_us']/1_000_000 if _int(q['end_us']) else None,
                             row['path']+'/lifecycle_qualification'])
    raw = json.dumps({'resource_reactions': audit, 'resource_reaction_source_adoption': adoption},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    rows.append(['完整只读记录', '连续拼接原文块可重建；修改动作/来源后须回App重算。'])
    rows.extend([[f'原文块 {i//480+1}', raw[i:i+480]] for i in range(0, len(raw), 480)])
    return rows


def reaction_resource_projection(row):
    """Exact joined identity for saved-result filtering; event_id is the parent."""
    projection = {'event_id': row['parent_event_id'], 'source_id': row['source_id'],
                  'actor': row['reaction_owner'], 'owner': row['source_owner'],
                  'status': row['state']}
    if _int(row['time_us']):
        projection['t'] = row['time_us'] / 1_000_000
    return projection
