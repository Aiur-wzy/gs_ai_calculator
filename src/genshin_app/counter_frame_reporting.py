"""Read-only projections of executed counter observations; no game inference."""
from __future__ import annotations

import json
import math


def is_self_frame_report(ledger):
    return isinstance(ledger, dict) and ledger.get('contract') == 'resource-counter-views-self-frame-v2'


def sampling_label(row):
    if row.get('frame_selection') == 'historical_reference':
        return '输出快照'
    if row.get('frame_selection') == 'current_no_self_reference':
        return '当前累计（未指定输出快照）'
    if row.get('domain') == 'provider' and row.get('sample') == 'current':
        return '提供者当前累计'
    return '取样口径未说明'


def gate_label(row):
    if ('gate_qualification' not in row and 'frame_selection' not in row
            and row.get('domain') == 'provider' and row.get('sample') == 'current'):
        return '提供者当前取样（资格见原资源读数）'
    gate = row.get('gate_qualification') or {}
    if not isinstance(gate, dict):
        return '门槛未说明'
    if not all(isinstance(gate.get(k), str) and gate[k] for k in ('event_run_id', 'instance_id')):
        return '门槛回执不完整'
    if gate['event_run_id'] != row.get('run_id') or gate['instance_id'] != row.get('instance_id'):
        return '门槛关联不一致'
    if gate.get('kind') == 'not_required':
        return '不需要次数门槛'
    if gate.get('kind') == 'shared_pool' and isinstance(gate.get('consumer_id'), str) and gate['consumer_id']:
        return '当前共享资源消费已通过：' + gate['consumer_id']
    if gate.get('kind') == 'quota' and isinstance(gate.get('rule_id'), str) and gate['rule_id']:
        return '当前有限次数已通过：' + gate['rule_id']
    return '门槛未说明'


def _raw_rows(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return [[f'原文块 {i // 480 + 1}', encoded[i:i + 480]] for i in range(0, len(encoded), 480)]


def _captures(ledger):
    capture = ledger.get('historical_capture')
    return capture.get('capture_trace', []) if isinstance(capture, dict) else []


def capture_index(captures):
    result = {}
    for row in captures:
        if isinstance(row.get('frame_id'), str):
            result.setdefault(row['frame_id'], []).append(row)
    return result


def capture_label(row, by_frame):
    if row.get('frame_selection') != 'historical_reference':
        return '无需历史捕获'
    frame = row.get('frame_id')
    matches = by_frame.get(frame, []) if isinstance(frame, str) else []
    if len(matches) != 1:
        return '捕获回执缺失' if not matches else '捕获回执不唯一'
    context = row.get('capture_context')
    if not isinstance(context, dict) or context != matches[0].get('context'):
        return '捕获关系不一致'
    time = context.get('time_us')
    seconds = None
    if type(time) is int:
        try:
            seconds = time / 1_000_000
        except OverflowError:
            pass
    when = str(seconds) + '秒' if seconds is not None and math.isfinite(seconds) else '时点未说明'
    return f"{when} / {context.get('event_id', '事件未说明')} / {row.get('frame_id')}"


def counter_view_rows(ledger):
    """Only the new report. Legacy formatting remains owned by its old caller."""
    if not is_self_frame_report(ledger):
        return []
    rows = [['资源读数流水', '只读实际取样。历史值与当前消费资格分开；合成收益已包含层数，不再额外计伤。'],
            ['秒数', '规则 / 收益', '接收角色', '取样口径', '历史捕获', '当前次数门槛',
             '累计 / 读数', '原始原子数', '每单位原子数', '采用读数', '单层表达式值',
             '层数口径', '层数', '合成收益', '资源游标 / 控制游标', '生命周期', '取样回执']]
    captures = capture_index(_captures(ledger))
    for row in ledger.get('trace', []):
        read = row.get('reading') or {}
        historical = row.get('frame_selection') == 'historical_reference'
        rows.append([row.get('t'), f"{row.get('rule_id','')} / {row.get('effect_id','')}", row.get('character'),
                     sampling_label(row), capture_label(row, captures), gate_label(row),
                     f"{read.get('counter_id','')} / {read.get('view_id','')}", read.get('raw_atoms'),
                     read.get('unit_scale'), row.get('source_value'), row.get('single_value', row.get('value')),
                     '捕获时冻结层数' if historical else '当前层数', row.get('layer_count'), row.get('combined_value'),
                     json.dumps(read.get('counter_cursor') or {}, ensure_ascii=False, sort_keys=True),
                     (read.get('lifecycle') or {}).get('id'), row.get('receipt_id')])
    rows += [[], ['完整只读资源读数账', '变更来源、动作、快照、面板或表达式后，回App重算。']]
    rows += _raw_rows(ledger)
    return rows


def counter_frame_rows(ledger):
    if not is_self_frame_report(ledger):
        return []
    capture = ledger.get('historical_capture')
    if not isinstance(capture, dict):
        return []
    rows = [['累计输出快照', '只读实际捕获，含附加检查窗口。捕获成功不代表后续命中已通过当前次数门槛；不增加伤害。'],
            ['秒数', '来源窗口', '捕获事件', '执行标识', '快照标识', '冻结成员数', '独立读数数',
             '读数定义指纹', '完整读者合同指纹']]
    for row in _captures(ledger):
        rows.append([row.get('t'), row.get('cycle'), row.get('event_id'), row.get('run_id'), row.get('frame_id'),
                     row.get('member_count'), row.get('reading_count'), row.get('program_fingerprint'),
                     row.get('reader_program_fingerprint')])
    usage = capture.get('usage') or {}
    rows += [[], ['捕获工作与存储', '编码检查字节包含失败准备；不等于已成功捕获次数。'],
             ['已提交快照数', usage.get('frames'), '已提交成员数', usage.get('members')],
             ['快照存储字节', capture.get('snapshot_storage_bytes'), '实际编码检查字节', capture.get('verification_bytes')],
             [], ['完整只读捕获账', '修改依赖后回App重新生成；本表不重演状态机。']]
    rows += _raw_rows(capture)
    return rows
