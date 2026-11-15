"""Agent-authored target statuses driven by the existing execution clock.

Duration, target membership and refresh policy are declarations, not inferred
from a field name or character. Actual reactions may trigger a declared rule.
"""
from __future__ import annotations

import copy
from decimal import Decimal

from genshin_engine.reaction_event_capabilities import OBSERVABLE_REACTION_TYPES
from genshin_engine.rotation_port import TICKS, rotation_issue
from genshin_engine.rotation_schema import EVIDENCE, REPEAT, STRING, arr, obj

_NONNEG = {"type": "number", "minimum": 0}
_OP = {"enum": ["apply", "refresh", "remove"]}
_COMMON = {"id": STRING, "target_id": STRING, "status_id": STRING,
           "operation": _OP, "duration": _NONNEG, **EVIDENCE}
TARGET_STATUS_EVENT_SCHEMA = obj({**_COMMON, "t": _NONNEG, "order": {"type": "integer"},
    "repeat": REPEAT, "source_event_id": STRING},
    ["id", "target_id", "status_id", "operation", "t", "order", "repeat", "status", "source"])
TARGET_STATUS_RULE_SCHEMA = obj({**_COMMON,
    "reaction_types": {**arr(STRING), "minItems": 1, "uniqueItems": True}},
    ["id", "target_id", "status_id", "operation", "reaction_types", "status", "source"])


def validate_status_declarations(model):
    issues, seen = [], set()
    for name in ("target_status_events", "target_status_rules"):
        for index, row in enumerate(model.get(name, [])):
            path = f"mechanics_model.{name}[{index}]"
            if row['id'] in seen:
                issues.append(rotation_issue('duplicate_id', '目标状态声明ID重复。', path+'.id'))
            seen.add(row['id'])
            if name == 'target_status_rules' and (len(set(row['reaction_types'])) != len(row['reaction_types'])
                    or not set(row['reaction_types']).issubset(OBSERVABLE_REACTION_TYPES)):
                issues.append(rotation_issue('reference', '反应状态规则必须引用已支持的反应类型，不使用未知名称。', path+'.reaction_types'))
            if row['status_id'] == 'element_freeze':
                issues.append(rotation_issue('reserved_id', 'element_freeze由冻结运行时提供；显式状态请使用独立ID。', path+'.status_id'))
            if row['operation'] == 'remove':
                if 'duration' in row:
                    issues.append(rotation_issue('unused_field', '移除状态不使用持续时间。', path+'.duration'))
            elif 'duration' not in row:
                issues.append(rotation_issue('missing_information', '施加或刷新状态需要持续时间。', path+'.duration'))
            for field in ('duration', 't'):
                if field in row and Decimal(str(row[field])) * TICKS % 1:
                    issues.append(rotation_issue('time_precision', '时间必须精确到微秒。', path+'.'+field))
    return issues


class TargetStatusRuntime:
    def __init__(self, model):
        declarations = [*model.get('target_status_events', []), *model.get('target_status_rules', [])]
        self.known = {(row['target_id'], row['status_id']) for row in declarations}
        self.rules = model.get('target_status_rules', [])
        self.state, self.trace, self.revision = {}, [], 0

    def apply(self, operation, *, time, event_id, cycle=0, reaction_type=None):
        key = (operation['target_id'], operation['status_id'])
        old = self.state.get(key)
        before = (None if old and old['status'] == 'pending'
                  else bool(old and old['expires_at'] > time))
        self.revision += 1
        expires = time if operation['operation'] == 'remove' else time + int(Decimal(str(operation['duration'])) * TICKS)
        # apply and refresh both replace this status's current generation;
        # independent sources should use independent status IDs and predicates.
        state = {'status': operation['status'], 'source': operation['source'],
                 'source_id': operation['id'], 'source_event_id': event_id,
                 'starts_at': time, 'expires_at': expires, 'revision': self.revision,
                 'evidence_tier': 'declared_reaction_status_execution' if reaction_type else 'declared_status_execution'}
        self.state[key] = state
        receipt = {'type': 'target_status', 'event_id': event_id, 'declaration_id': operation['id'],
            'cycle': cycle, 't': time/TICKS, 'target_id': key[0], 'status_id': key[1],
            'operation': operation['operation'], 'before_active': before,
            'after_active': None if operation['status'] == 'pending' else expires > time,
            'expires_at': expires/TICKS, 'revision': self.revision,
            'source': operation['source'], 'source_status': operation['status'],
            'evidence_tier': state['evidence_tier'], 'reaction_type': reaction_type}
        self.trace.append(receipt)
        return receipt

    def reactions(self, rows, *, target_id, time, cycle):
        for row in rows:
            kind = row.get('replacement_type') or row.get('type')
            tags = row.get('reaction_trigger_tags') or [kind]
            for rule in self.rules:
                if rule['target_id'] == target_id and set(tags) & set(rule['reaction_types']):
                    self.apply(rule, time=time, event_id=row.get('run_id', row.get('id', 'unknown')),
                               cycle=cycle, reaction_type=kind)

    def observe(self, target_id, time):
        observations = {}
        for target, status_id in sorted(self.known):
            if target != target_id:
                continue
            state = self.state.get((target, status_id))
            observations[status_id] = ({'active': None if state['status'] == 'pending' else state['expires_at'] > time,
                **copy.deepcopy(state), 'starts_at': state['starts_at']/TICKS, 'expires_at': state['expires_at']/TICKS}
                if state else {'active': False, 'revision': 0, 'evidence_tier': 'declared_status_execution',
                               'reason': 'no_declared_transition_yet'})
        return observations
