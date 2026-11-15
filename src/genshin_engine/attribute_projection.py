"""Closed projection facts for graph v2; callers still own frame authority.

Projection rows preserve old member qualification while allowing only an
explicit event-sampled input to bind to a later, executed observation. A damage
projection is never an ordinary property that another expression may consume.
"""
from __future__ import annotations

import copy

from genshin_engine.attribute_ledger import AttributeLedgerError, _issue
from genshin_engine.attribute_schema import FRAME, ID
from genshin_engine.rotation_schema import obj

SAMPLE_BINDING = obj({'mode': {'const': 'event'}, 'event_id': ID,
                      'origin_scope': {'const': 'evaluation_event'}}, ['mode', 'event_id', 'origin_scope'])
PROJECTION_OF = obj({'frame_id': ID, 'contribution_id': ID}, ['frame_id', 'contribution_id'])
OBSERVATION = obj({'kind': {'const': 'executed_event'}, 'event_id': ID,
                   'origin': FRAME['properties']['origin'], 'actual_event_run_id': ID},
                  ['kind', 'event_id', 'origin', 'actual_event_run_id'])
PROPERTY_FRAME = copy.deepcopy(FRAME)
PROPERTY_FRAME['properties'].update(frame_kind={'const': 'property'}, observation=OBSERVATION)
PROJECTION_FRAME = copy.deepcopy(FRAME)
PROJECTION_FRAME['properties'].update(
    frame_kind={'const': 'damage_projection'}, phase={'const': 'damage_projection'},
    context=obj({'actor': ID, 'actual_event_run_id': ID, 'self_frame_id': ID, 'current_frame_id': ID},
                ['actor', 'actual_event_run_id', 'self_frame_id', 'current_frame_id']))
PROJECTION_FRAME['required'].extend(['frame_kind', 'context'])
GRAPH_FRAME = {'oneOf': [PROPERTY_FRAME, PROJECTION_FRAME]}


def is_projection(frame):
    return frame.get('frame_kind', 'property') == 'damage_projection'


def validate_frame(frame):
    observation = frame.get('observation')
    if observation is not None:
        actual = observation['actual_event_run_id']
        qualified = actual if actual.startswith(f"{frame['origin']}:") else f"{frame['origin']}:{actual}"
        if (observation['origin'] != frame['origin'] or observation['event_id'] != frame['event_id']
                or qualified != frame['run_id']):
            raise AttributeLedgerError('attribute_observation_identity', '已执行观察必须保留相同事件、origin与真实run身份', frame['id'])
    if is_projection(frame):
        actual = frame['context']['actual_event_run_id']
        qualified = actual if actual.startswith(f"{frame['origin']}:") else f"{frame['origin']}:{actual}"
        if qualified != frame['run_id']:
            raise AttributeLedgerError('attribute_projection_identity', '投影必须保留实际出伤run身份', frame['id'])


def historical_issue(source, current, path):
    if source['id'] == current['id']:
        return None
    if source['t_us'] > current['t_us'] or source['state_epoch'] > current['state_epoch'] or source['origin'] > current['origin']:
        return _issue('attribute_forward_frame', '转换不得读取尚未发生的未来帧', path, status='pending')
    if source['state_epoch'] == current['state_epoch']:
        return _issue('attribute_frame_order_unknown', '不同帧同epoch不能证明历史顺序', path, status='pending')
    return None


def context_issue(query, frame, frames):
    projection = is_projection(frame)
    if not projection:
        return (_issue('attribute_projection_context', '属性帧不能伪作出伤投影根', frame['id'], status='pending')
                if query['purpose'] == 'damage_projection' else None)
    context = frame['context']
    if query['purpose'] != 'damage_projection' or query['basis'] != 'total' or query['actor'] != context['actor']:
        return _issue('attribute_projection_context', '投影只允许同一出伤角色的TOTAL根读取', frame['id'], status='pending')
    for field in ('self_frame_id', 'current_frame_id'):
        source = frames.get(context[field])
        if source is None:
            return _issue('attribute_frame_missing', '投影来源属性帧尚未提供', context[field], status='pending')
        if is_projection(source):
            return _issue('attribute_projection_source', '投影资格只能来自普通属性帧', context[field])
        issue = historical_issue(source, frame, context[field])
        if issue:
            return issue
    return None


def node_issue(node, query, frames, nodes):
    frame = query['frame']
    projected = is_projection(frame)
    ref = node.get('projection_of')
    if not projected and ref is None:
        return None

    def bad():
        return _issue('attribute_projection_source', '投影必须保留真实来源成员、输出域、实例层和数值运算，仅授权event可重绑输入帧', node['id'])

    if not projected or ref is None:
        return bad()
    source = frames.get(ref['frame_id'])
    if source is None:
        return _issue('attribute_frame_missing', '投影原始资格帧尚未提供', ref['frame_id'], status='pending')
    original = nodes.get(ref['contribution_id'])
    if original is None or ref['contribution_id'] not in source['contribution_ids'] or 'projection_of' in original:
        return bad()
    domain = original.get('output_domain')
    if domain not in ('self', 'provider') or source['id'] != frame['context'][f'{domain if domain == "self" else "current"}_frame_id']:
        return bad()
    # Comparing the complete row preserves future closed fields as well.
    expected = {k: v for k, v in original.items() if k != 'id'}
    actual = {k: v for k, v in node.items() if k not in ('id', 'projection_of')}
    binding = original.get('sample_binding')
    if binding is None:
        return None if expected == actual else bad()
    if not isinstance(node['value'], dict) or not isinstance(original['value'], dict):
        return bad()
    expected = copy.deepcopy(expected)
    expected['value']['input']['frame_id'] = node['value']['input']['frame_id']
    if expected != actual:
        return bad()
    target = frames.get(node['value']['input']['frame_id'])
    if target is None:
        return _issue('attribute_frame_missing', '指定事件尚未实际执行捕获', node['value']['input']['frame_id'], status='pending')
    observation = target.get('observation', {})
    if (is_projection(target) or observation.get('kind') != 'executed_event'
            or observation.get('event_id') != binding['event_id'] or observation.get('origin') != frame['origin']):
        return _issue('attribute_event_observation_missing', '指定事件须在当前出伤origin中真实执行并留下观察帧', node['id'], status='pending')
    return historical_issue(target, frame, node['id'])
