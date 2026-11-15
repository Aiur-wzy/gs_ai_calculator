"""Bounded read-only views of diagnostics, including executions with no damage result."""
from __future__ import annotations

import copy
import hashlib
import json
import math

CONTRACT = 'mechanics-diagnostics-v1'
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ISSUES = 100000
IDENTITY = ('code', 'severity', 'path', 'condition', 'rule_id', 'effect_id', 'source_id')
FILTERS = (*IDENTITY, 'event_id', 'cycle', 'phase', 'group_id')


class DiagnosticError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                          allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise DiagnosticError('invalid_diagnostics', '诊断须为有限JSON。') from exc


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _clip(value, limit=256):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]+'…'
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:8]]
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in list(value.items())[:16]}
    return value


class DiagnosticIndex:
    """Indexes immutable raw issues. Does not infer missing rule, phase or trace links."""

    def __init__(self, issues, *, status, pointer='/issues'):
        if (not isinstance(issues, list) or len(issues) > MAX_ISSUES
                or any(not isinstance(row, dict) for row in issues)):
            raise DiagnosticError('invalid_diagnostics', '诊断列表结构或数量超出范围。')
        if len(_json(issues)) > MAX_INPUT_BYTES:
            raise DiagnosticError('diagnostic_input_limit', '诊断超过只读索引的字节上限。')
        if not isinstance(status, str) or len(status) > 64 or not isinstance(pointer, str) or len(pointer) > 512 or not pointer.startswith('/'):
            raise DiagnosticError('invalid_diagnostics', '须明确执行状态和原JSON位置。')
        self._issues = copy.deepcopy(issues)
        self.status, self.pointer = status, pointer
        self.fingerprint = _hash({'contract': CONTRACT, 'issues': issues})
        self._groups, self._ids = {}, []
        for index, issue in enumerate(self._issues):
            identity = {key: issue[key] for key in IDENTITY if key in issue}
            gid = 'issue:'+_hash(identity)
            self._ids.append(gid)
            self._groups.setdefault(gid, {'identity': identity, 'indices': []})['indices'].append(index)

    def _example(self, index):
        row = self._issues[index]
        return {'index': index, 'json_pointer': f'{self.pointer}/{index}',
                'excerpt': _clip(row), 'excerpt_complete': _clip(row) == row,
                'correlation': 'raw_issue_only; no inferred trace or input-rule identity'}

    def _group(self, gid, indices):
        samples = list(dict.fromkeys([indices[0], *indices[1:2], indices[-1]]))
        rows = [self._issues[i] for i in indices]
        cycles = [r['cycle'] for r in rows if type(r.get('cycle')) is int]
        times = [r['t'] for r in rows if type(r.get('t')) in (int, float) and math.isfinite(r['t'])]
        identity = self._groups[gid]['identity']
        return {'group_id': gid, 'identity': _clip(identity), 'identity_complete': _clip(identity) == identity,
                'count': len(indices), 'first_issue_index': indices[0], 'last_issue_index': indices[-1],
                'cycle_range': [min(cycles), max(cycles)] if cycles else None,
                'unknown_cycle_count': len(rows)-len(cycles),
                'time_range': [min(times), max(times)] if times else None,
                'unknown_time_count': len(rows)-len(times),
                'unknown_phase_count': sum(not isinstance(r.get('phase'), str) for r in rows),
                'examples': [self._example(i) for i in samples]}

    def page(self, *, view='groups', filters=None, limit=50, cursor=None):
        filters = {} if filters is None else filters
        if view not in ('groups', 'issues') or type(limit) is not int or not 1 <= limit <= 200:
            raise DiagnosticError('invalid_diagnostic_query', '视图须为groups/issues，页长为1至200。')
        if not isinstance(filters, dict) or set(filters)-set(FILTERS):
            raise DiagnosticError('invalid_diagnostic_query', '不支持的诊断筛选。')
        for key, value in filters.items():
            if key == 'cycle':
                valid = type(value) is int and value >= 0
            else:
                valid = isinstance(value, str) and 0 < len(value) <= 512
            if not valid:
                raise DiagnosticError('invalid_diagnostic_query', '诊断筛选值不合法。')
        selection = _hash({'view': view, 'filters': filters, 'pointer': self.pointer, 'status': self.status})
        offset = 0
        if cursor is not None:
            if (not isinstance(cursor, dict) or set(cursor) != {'contract', 'fingerprint', 'selection', 'offset'}
                    or cursor.get('contract') != CONTRACT or cursor.get('fingerprint') != self.fingerprint
                    or cursor.get('selection') != selection):
                raise DiagnosticError('stale_diagnostic_cursor', '诊断、执行状态或筛选已变化，请重新开始查询。')
            offset = cursor['offset']
            if type(offset) is not int or offset < 0:
                raise DiagnosticError('invalid_diagnostic_query', '分页偏移不合法。')
        indices = [i for i, row in enumerate(self._issues) if all(
            (key != 'cycle' or type(row.get(key)) is int)
            and (self._ids[i] if key == 'group_id' else row.get(key)) == value
            for key, value in filters.items())]
        if view == 'groups':
            matched = {}
            for i in indices:
                matched.setdefault(self._ids[i], []).append(i)
            keys = sorted(matched)
            total = len(keys)
            row_at = lambda i: self._group(keys[i], matched[keys[i]])
        else:
            total = len(indices)
            row_at = lambda i: {'group_id': self._ids[indices[i]], **self._example(indices[i])}
        if offset > total:
            raise DiagnosticError('invalid_diagnostic_query', '分页偏移超过当前筛选范围。')
        response = {'contract': CONTRACT, 'execution_status': self.status, 'view': view,
                    'source_issues_fingerprint': self.fingerprint, 'raw_json_pointer': self.pointer,
                    'raw_issue_count': len(self._issues), 'matched_issue_count': len(indices),
                    'total': total, 'offset': offset, 'items': [], 'next_cursor': None,
                    'interpretation': '只读诊断聚合；不改变执行状态，不证明机制完整或问题已修复。'}
        for i in range(offset, min(total, offset+limit)):
            response['items'].append(row_at(i))
            # Reserve space for cursor and final metadata; raw input remains intact.
            if len(_json(response)) > MAX_RESPONSE_BYTES-1024:
                response['items'].pop()
                break
        next_offset = offset+len(response['items'])
        if next_offset < total:
            if not response['items']:
                raise DiagnosticError('diagnostic_response_limit', '单组摘要超过响应上限，请按issues视图查询。')
            response['next_cursor'] = {'contract': CONTRACT, 'fingerprint': self.fingerprint,
                                       'selection': selection, 'offset': next_offset}
        return response


def diagnostics_contract():
    return {'contract': CONTRACT, 'views': ['groups', 'issues'], 'filters': list(FILTERS),
            'limits': {'input_bytes': MAX_INPUT_BYTES, 'response_bytes': MAX_RESPONSE_BYTES,
                       'issues': MAX_ISSUES, 'page_size': 200},
            'scope': '可检查pending/invalid且未产生伤害的执行；保存原issues，摘要不推断缺失phase/cycle或规则映射。'}
