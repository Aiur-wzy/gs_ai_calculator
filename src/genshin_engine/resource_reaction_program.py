"""RQ1 pure declaration compiler; no scheduler or game-source authority.

This off-tree slice does not deliver an execution token or certify occurrences.
The later adapter must bind the actual ledger/queue and preserve their commits.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET
from genshin_engine.shared_pools import SharedPoolError, validate_shared_pools

CONTRACT = 'ordinary-reaction-resource-v1'
LIMITS = {'graph_bytes': 8 * 1024 * 1024, 'graph_nodes': 200000, 'depth': 64,
          'string_chars': 65536, 'program_bytes': 256 * 1024, 'operations': 256}
SELECTOR_FIELDS = frozenset({'event', 'phase', 'reaction_types', 'reaction_elements', 'characters', 'owner_presence'})
REACTION_TYPES = frozenset({'melt', 'vaporize', 'swirl'})
REACTION_ELEMENTS = {
    DYNAMIC_SWIRL_RULESET: frozenset({'pyro', 'hydro', 'cryo'}),
    MULTI_AURA_RULESET: frozenset({'pyro', 'hydro', 'cryo', 'electro'}),
}


class ReactionResourceError(SharedPoolError):
    def __init__(self, code, message, path='resource_reaction_contract', *, severity='error'):
        ValueError.__init__(self, message)
        self.issue = {'code': code, 'message': message, 'path': path, 'severity': severity}


def _fail(code, message, path='resource_reaction_contract', *, severity='error'):
    raise ReactionResourceError(code, message, path, severity=severity)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _bounded(value, *, byte_limit=None, charge_work=None):
    """Bound work, depth and bytes before whole-object copy/serialization/hash."""
    limit = LIMITS['graph_bytes'] if byte_limit is None else byte_limit
    active, nodes, byte_count = set(), 0, 0

    def charge(size):
        nonlocal byte_count
        byte_count += size
        if byte_count > limit:
            _fail('resource_reaction_limit', '反应资源声明字节超过限制。', severity='pending')

    def visit(item, depth):
        nonlocal nodes
        if charge_work is not None:
            charge_work(1)  # Stop before touching this node when work is gone.
        nodes += 1
        if nodes > LIMITS['graph_nodes'] or depth > LIMITS['depth']:
            _fail('resource_reaction_limit', '反应资源声明节点或深度超过限制。', severity='pending')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active:
                _fail('resource_reaction_schema', '反应资源声明不能含循环引用。')
            if len(item) > LIMITS['graph_nodes']:
                _fail('resource_reaction_limit', '反应资源声明容器超过限制。', severity='pending')
            active.add(id(item))
            if charge_work is not None:
                charge_work(math.ceil((2 + max(0, len(item) - 1)) / 128))
            charge(2 + max(0, len(item) - 1))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        _fail('resource_reaction_schema', '反应资源声明键必须为字符串。')
                    visit(key, depth + 1)
                    charge(1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(id(item))
            return
        if kind is str and len(item) > LIMITS['string_chars']:
            _fail('resource_reaction_limit', '反应资源声明文本超过限制。', severity='pending')
        if kind is int and item.bit_length() > 4096:
            _fail('resource_reaction_limit', '反应资源声明整数超过限制。', severity='pending')
        if kind not in (str, int, float, bool, type(None)) or kind is float and not math.isfinite(item):
            _fail('resource_reaction_schema', '反应资源声明必须为有限JSON。')
        if charge_work is not None:
            # Conservative UTF-8/JSON expansion reservation before encoding.
            upper = len(item) * 6 + 2 if kind is str else item.bit_length() + 2 if kind is int else 32
            charge_work(math.ceil(upper / 128))
        try:
            encoded_size = len(_canonical(item))
        except (UnicodeError, ValueError, OverflowError):
            _fail('resource_reaction_schema', '反应资源声明编码或数值无效。')
        charge(encoded_size)

    visit(value, 0)
    return {'nodes': nodes, 'bytes': byte_count}


def _ids(value, *, allowed=None, maximum=256, path='operation_ids'):
    if (type(value) is not list or not 1 <= len(value) <= maximum
            or any(type(v) is not str or not v.strip() or len(v) > 128 for v in value)
            or len(set(value)) != len(value)
            or allowed is not None and not set(value) <= allowed):
        _fail('resource_reaction_reference', '反应资源身份集合为空、重复、超限或不在范围内。', path)
    return sorted(value)


def _raise_first(issues):
    if issues:
        issue = issues[0]
        _fail(issue['code'], issue['message'], issue.get('path', 'mechanics_model'), severity=issue.get('severity', 'error'))


def reaction_ruleset(model):
    elemental = model.get('elemental_model')
    ruleset = elemental.get('ruleset') if type(elemental) is dict else None
    if type(ruleset) is not str or ruleset not in REACTION_ELEMENTS:
        _fail('resource_reaction_model', '反应资源合同需要普通动态v3或多元素v4模型。')
    return ruleset


def reaction_members(application, ruleset):
    """Select the authoritative reaction representation for the elemental model."""
    if ruleset == MULTI_AURA_RULESET:
        reactions = application.get('reactions')
        if type(reactions) is not list:
            _fail('resource_reaction_actual_identity', '多元素实际附着缺少反应列表。', severity='pending')
        return reactions
    reaction = application.get('reaction')
    return [reaction] if reaction else []


def valid_v4_reaction_member(application, reaction, index):
    if (reaction.get('ruleset') != MULTI_AURA_RULESET
            or reaction.get('trigger_element') != application['element']):
        return False
    if reaction['type'] not in REACTION_TYPES:
        return True
    # Swirl goes through proposal routing. Amplifying reactions are built
    # directly by their resolver after committing the consumed aura.
    routed = reaction['type'] == 'swirl'
    return (type(reaction.get('route_index')) is int and reaction['route_index'] == index
            and reaction.get('route', None if routed else 'ordinary') == 'ordinary'
            and reaction.get('commit_state', None if routed else 'committed') == 'committed')


def validate_reaction_selector(selector, ruleset, path='resource_reaction_contract'):
    if (set(selector) - SELECTOR_FIELDS or selector['event'] != 'elemental_reaction'
            or selector['phase'] != 'post'):
        _fail('resource_reaction_scope', '反应资源selector含范围外字段或事件阶段。', path)
    _ids(selector.get('reaction_types'), allowed=REACTION_TYPES, maximum=len(REACTION_TYPES),
         path=path + '.reaction_types')
    if 'reaction_elements' in selector:
        elements = REACTION_ELEMENTS[ruleset]
        _ids(selector['reaction_elements'], allowed=elements, maximum=len(elements),
             path=path + '.reaction_elements')


def _graph_fingerprint(model, characters):
    # Membership and character lists have set semantics. Other input arrays are
    # preserved; this hash does not reorder game events or source deliveries.
    normalized = dict(model)
    contract = dict(model['resource_reaction_contract'])
    contract['operation_ids'] = sorted(contract['operation_ids'])
    normalized['resource_reaction_contract'] = contract
    return hashlib.sha256(_canonical({'model': normalized, 'characters': sorted(characters)})).hexdigest()


def declaration(model):
    contract = model.get('resource_reaction_contract')
    if type(contract) is not dict or type(contract.get('schema_version')) is not int:
        _fail('resource_reaction_schema', '反应资源合同需要显式版本。')
    version = contract['schema_version']
    fields = {'schema_version', 'operation_ids'} | ({'lifecycle_gates'} if version == 2 else set())
    if version not in (1, 2) or set(contract) != fields:
        _fail('resource_reaction_schema', '反应资源合同版本或字段无效。')
    selected = _ids(contract['operation_ids'])
    gates = contract.get('lifecycle_gates', [])
    if type(gates) is not list or len(gates) > 256:
        _fail('resource_lifecycle_schema', '生命周期资格目录超限或无效。')
    if gates:
        state = model.get('state_runtime', {})
        if state.get('schema_version') != 2:
            _fail('resource_lifecycle_schema', '生命周期资格需要显式state2实际控制。')
        operations = {r['id']: r for r in model.get('shared_pools', {}).get('operations', [])}
        pools = {r['id']: r for r in model.get('shared_pools', {}).get('pools', [])}
        lives = {r['id']: r for r in state.get('lifecycles', [])}
        seen = set()
        for gate in gates:
            if (type(gate) is not dict or set(gate) != {'operation_id', 'lifecycle_id', 'window', 'generation_policy'}
                    or any(type(v) is not str for v in gate.values())):
                _fail('resource_lifecycle_schema', '生命周期资格字段无效。')
            oid, lid = gate['operation_id'], gate['lifecycle_id']
            if oid in seen or oid not in selected or oid not in operations or lid not in lives:
                _fail('resource_lifecycle_reference', '生命周期资格引用不存在或重复。')
            seen.add(oid)
            pool = pools.get(operations[oid]['pool_id'])
            if pool is None or pool['owner'] != lives[lid]['owner']:
                _fail('resource_lifecycle_owner', '生命周期与资源池的归属必须一致。')
            if gate['window'] != 'established_until_end' or gate['generation_policy'] != 'current_at_resource_attempt':
                _fail('resource_lifecycle_policy', '生命周期资格策略不在已审范围内。')
    return version, selected, gates


class Program:
    def __init__(self, model, characters):
        self.input_usage = _bounded(model)
        _bounded(characters, byte_limit=16384)
        if type(model) is not dict:
            _fail('resource_reaction_schema', '反应资源合同需要完整机制对象。')
        chars = _ids(characters, maximum=16, path='characters')
        version, selected, gates = declaration(model)
        if version == 2 and not gates:
            legacy = {**model, 'resource_reaction_contract': {'schema_version': 1, 'operation_ids': selected}}
            self.input_usage = _bounded(legacy)  # Empty opt-in has no qualification work.

        self.ruleset = reaction_ruleset(model)
        if model.get('application_events'):
            _fail('resource_reaction_parent', '本片反应资源合同不接受独立无伤害附着父。', severity='pending')
        if 'shared_pools' not in model:
            _fail('resource_reaction_reference', '反应资源合同缺少共享池。')
        # The ordinary engine remains the authority for existing compatible
        # state/C2/threshold scopes; this compiler adds no blanket exclusion.
        from genshin_engine.mechanics import validate_mechanics_model
        original_model = {k: v for k, v in model.items() if k != 'resource_reaction_contract'}
        _raise_first(validate_mechanics_model(original_model)['issues'])
        _raise_first(validate_shared_pools(model['shared_pools'], character_ids=chars,
                                          rule_ids=[r['id'] for r in model['rules']]))
        pools = {row['id']: row for row in model['shared_pools']['pools']}
        operations = {row['id']: row for row in model['shared_pools']['operations']}
        if not set(selected) <= set(operations):
            _fail('resource_reaction_reference', '反应资源operation引用不存在。')
        records = []
        for oid in selected:
            operation = operations[oid]
            selector = operation['trigger']
            path = 'shared_pools.operations.' + oid
            if operation['operation'] != 'grant' or type(operation['amount']) is not int:
                _fail('resource_reaction_scope', '反应资源首片仅接受常量grant。', path)
            validate_reaction_selector(selector, self.ruleset, path + '.trigger')
            if 'characters' in selector:
                _ids(selector['characters'], allowed=set(chars), maximum=16, path=path + '.trigger.characters')
            pool = pools[operation['pool_id']]
            keys = (['team'] if pool['scope'] == 'team' else chars) if operation['targets']['mode'] == 'all_pool_keys' else sorted(operation['targets']['characters'])
            records.append({'id': oid, 'pool_id': pool['id'], 'source_id': operation['source_id'],
                            'source_owner': pool['owner'], 'keys': keys, 'operation': operation, 'pool': pool,
                            'selector': {**selector, 'owner_presence': selector.get('owner_presence', 'any')}})
        manifest = {'contract': CONTRACT, 'characters': chars, 'operations': records,
                    'source_authority': 'not_evaluated', 'execution': 'not_connected',
                    'verification_scope': 'declared_configuration_consistency_only'}
        if version == 2:
            manifest['contract'] = 'ordinary-reaction-resource-v2'
        if gates:
            manifest['lifecycle_gates'] = gates
        if self.ruleset == MULTI_AURA_RULESET:
            manifest['elemental_ruleset'] = self.ruleset
        self.fingerprint = _graph_fingerprint(model, chars)
        manifest['program_fingerprint'] = self.fingerprint
        _bounded(manifest, byte_limit=LIMITS['program_bytes'])
        self._manifest = copy.deepcopy(manifest)
        self._records = {row['id']: row for row in self._manifest['operations']}
        self._materials = {row['id']: _canonical({'operation': row['operation'], 'pool': row['pool']}) for row in records}

    def report(self):
        return copy.deepcopy(self._manifest)

    def verify_model(self, model, characters, *, charge_work=None):
        measured = model
        contract = model.get('resource_reaction_contract', {})
        if contract.get('schema_version') == 2 and contract.get('lifecycle_gates') == []:
            measured = {**model, 'resource_reaction_contract': {
                'schema_version': 1, 'operation_ids': contract['operation_ids']}}
        _bounded(measured, charge_work=charge_work)
        if charge_work is not None:
            _bounded(characters, byte_limit=16384, charge_work=charge_work)
        chars = _ids(characters, maximum=16, path='characters')
        try:
            same = _graph_fingerprint(model, chars) == self.fingerprint
        except (KeyError, TypeError, ValueError):
            same = False
        if not same:
            _fail('resource_reaction_program_changed', '实际执行图或角色集合与初始化合同不一致。')

    def verify_operation(self, operation_id, operation, pool):
        _bounded({'operation': operation, 'pool': pool}, byte_limit=LIMITS['program_bytes'])
        if type(operation_id) is not str:
            _fail('resource_reaction_reference', '实际资源operation身份无效。')
        original = self._records.get(operation_id)
        if original is None or self._materials[operation_id] != _canonical({'operation': operation, 'pool': pool}):
            _fail('resource_reaction_program_changed', '实际operation或pool与初始化合同不一致。')
        return list(original['keys'])


def validate_bindings(model, characters):
    if type(model) is dict and 'resource_reaction_contract' not in model:
        return []
    try:
        Program(model, characters)
    except ReactionResourceError as exc:
        return [copy.deepcopy(exc.issue)]
    return []
