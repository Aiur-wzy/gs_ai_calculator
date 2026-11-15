"""Read-only projection of actual threshold traces, never of issued commands."""
from __future__ import annotations

import math

from genshin_app.equipment.schema import PlanError

FIELDS = ('resource_threshold_observers', 'resource_threshold_triggers')
TITLES = ('资源阈值观察', '阈值收益执行')
COMPARISONS = {'enter_gte': '向上跨过或达到', 'enter_lte': '向下跨过或达到'}
OUTCOMES = {'activated': '收益已激活', 'not_activated': '未激活（条件不满足或冷却中）',
            'lifecycle_started': '领域已开始', 'lifecycle_kept': '保留原领域',
            'lifecycle_ended': '领域已结束', 'no_active_instance': '没有活动领域，无需结束',
            'pending': '待核实', 'invalid': '无效', 'failed': '失败'}


def threshold_time_seconds(item, field):
    key = 't_us' if field == FIELDS[0] else 'time_us'
    if key not in item:
        return None
    value = item[key]
    try:
        if type(value) is int:
            seconds = value / 1_000_000
            if math.isfinite(seconds):
                return seconds
    except OverflowError:
        pass
    raise PlanError('阈值流水时间须为有限整数微秒', 'invalid_review_input')


def threshold_projection(item, ledger, field):
    """Join only an unambiguous exact source identity; retain original receipts."""
    row = dict(item)
    ident_key = 'observer_id' if field == FIELDS[0] else 'binding_id'
    ident = item.get(ident_key)
    records = ledger.get('source_adoptions', [])
    if isinstance(ident, str) and isinstance(records, list):
        matches = [record for record in records if isinstance(record, dict) and record.get('id') == ident]
        if len(matches) == 1 and matches[0].get('status') == 'ready':
            for key in ('source_id', 'owner', 'name_zh'):
                if key in matches[0]:
                    row.setdefault(key, matches[0][key])
    if field == FIELDS[1] and 'source_event_id' in item:
        row['event_id'] = item['source_event_id']
    return row


def threshold_outcome(row, field):
    if field == FIELDS[0]:
        return '已跨越阈值' if row.get('crossed') is True else '未跨越阈值' if row.get('crossed') is False else '观察结果未知'
    if row.get('type') == 'threshold_dispatch_failed':
        detail = row.get('message') or row.get('code')
        return '派发失败：' + OUTCOMES.get(row.get('outcome'), '结果未知') + (f'；{detail}' if detail else '')
    if row.get('type') != 'threshold_dispatch_completed':
        return '执行过程未知'
    return OUTCOMES.get(row.get('outcome'), '执行结果未知')
