"""Closed actual RQ2 declaration compiler; no source/game qualification."""
from __future__ import annotations

from genshin_engine.resource_definition_index import DefinitionIndex, IndexError
from genshin_engine.resource_lifecycle_qualification import (
    QualificationError,
    QualificationService,
    _exact,
    _text,
)

COUNTER_FIELDS = ('_config', '_pool_config', '_pools', '_ops', '_consumers', '_lives', '_counters', '_fingerprint')
RULE_FIELDS = frozenset(('id', 'name_zh', 'owner', 'source_id', 'trigger', 'beneficiaries',
    'timing', 'stacking', 'quota', 'resource_gate', 'effects', 'resources', 'status', 'source'))
RULE_ROUTING = ('trigger', 'beneficiaries', 'quota', 'resource_gate', 'resources')


def roots_of(runtime):
    clock, pool, ledger = runtime.clock, runtime.pool_runtime, runtime.ledger
    roots = {'model': runtime.ctx.model, 'rules': runtime.ctx.rules, 'gates': runtime.gates,
        'config': clock.config, 'lives': clock.lives, 'timers': clock.timers, 'by_life': clock.by_life,
        'pool_config': pool.config, 'runtime_pools': pool.pools,
        'ledger_operations': ledger._operations, 'ledger_pools': ledger._pools,
        'ledger_consumers': ledger._consumers}
    if runtime.counter_program is not None:
        for key in COUNTER_FIELDS:
            roots['counter' + key] = getattr(runtime.counter_program, key)
    return roots


class _Compiler:
    """Prepaid bounded temporary references; reservations are nonrefundable."""
    def __init__(self, budget):
        budget.pay(work=1, storage=512)
        self.budget = budget
        self.references = 0

    def list(self, source=()):
        self.budget.pay(work=1+len(source), storage=128+64*len(source))
        self.references += len(source)
        self.limit()
        return list(source)

    def mapping(self, rows=()):
        self.budget.pay(work=1+len(rows), storage=256+256*len(rows))
        self.references += len(rows)
        self.limit()
        return {row['id']: row for row in rows}

    def set(self, values=()):
        self.budget.pay(work=1+len(values), storage=256+128*len(values))
        self.references += len(values)
        self.limit()
        return set(values)

    def limit(self):
        if self.references > 1_000_000:
            raise IndexError('index_limit', 'Compiler temporary reference growth exceeded')

    def append(self, target, value):
        self.budget.pay(work=1, storage=64)
        self.references += 1
        self.limit()
        target.append(value)

    def extend(self, target, values):
        self.budget.pay(work=1+len(values), storage=64*len(values))
        self.references += len(values)
        self.limit()
        target.extend(values)

    def add(self, target, value):
        self.budget.pay(work=1, storage=128)
        self.references += 1
        self.limit()
        target.add(value)

    def store(self, target, key, value):
        self.budget.pay(work=1, storage=256)
        self.references += 1
        self.limit()
        target[key] = value

    def containers(self, values):
        pending, seen, result = self.list(values), self.set(), self.list()
        while pending:
            self.budget.pay(work=1)
            node = pending.pop()
            if type(node) not in (dict, list) or id(node) in seen:
                continue
            if len(seen) >= self.budget.container_limit:
                raise IndexError('index_limit', 'Compiler container limit')
            self.add(seen, id(node))
            self.append(result, node)
            self.extend(pending, node.values() if type(node) is dict else node)
        return result

    def source_rules(self, pending, rules, source_ids):
        for row in rules.values():
            self.budget.pay(work=1)
            if row['source_id'] in source_ids:
                self.append(pending, row['id'])


def compile_index(runtime):
    budget = runtime.budget
    # Root wrappers are fixed <= 128 entries and also covered by bootstrap
    # scratch on prepare/admit/final calls; compiler's retained wrapper is paid.
    budget.pay(work=32, storage=16384)
    roots = roots_of(runtime)
    audit = runtime.reaction.audit
    # Preflight is performed by the same complete JSON validator before this
    # typed compiler can traverse. It retains, validates and meters all text.
    from genshin_engine.resource_reaction_program import _bounded
    _bounded(roots, byte_limit=8*1024*1024, charge_work=audit._charge)
    from genshin_engine.mechanics_schema import MECHANICS_SCHEMA
    build = _Compiler(budget)
    model_keys = build.set(runtime.ctx.model)
    schema_keys = build.set(MECHANICS_SCHEMA['properties'])
    if not model_keys <= schema_keys:
        raise IndexError('index_schema', 'Unknown engine routing root')
    non_rules = build.list()
    for name, root in roots.items():
        budget.pay(work=1)
        if name == 'model':
            for key, value in root.items():
                budget.pay(work=1)
                if key != 'rules':
                    build.append(non_rules, value)
        elif name != 'rules':
            build.append(non_rules, root)
    spine = build.containers(non_rules)
    build.extend(spine, (runtime.ctx.model, runtime.ctx.model['rules'], runtime.ctx.rules))
    rules = build.list(runtime.ctx.model['rules'])
    build.extend(rules, runtime.ctx.rules.values())
    for row in rules:
        budget.pay(work=1+len(row))
        if type(row) is not dict or not build.set(row) <= RULE_FIELDS:
            raise IndexError('index_schema', 'Unknown engine routing rule')
        build.append(spine, row)
        routing = build.list()
        for key in RULE_ROUTING:
            budget.pay(work=1)
            if key in row:
                build.append(routing, row[key])
        build.extend(spine, build.containers(routing))
    scopes = build.mapping()
    operations = build.mapping(runtime.pool_runtime.config['operations'])
    consumers = build.mapping(runtime.pool_runtime.config['consumers'])
    for oid, gate in runtime.gates.items():
        budget.pay(work=1)
        life = runtime.clock.lives[gate['lifecycle_id']]
        operation = operations[oid]
        pool = runtime.pool_runtime.pools[operation['pool_id']]
        seeds = build.list((gate, life, operation, pool, runtime.clock.by_life[life['id']]))
        source_ids = build.set((life['source_id'], pool['source_id'], operation['source_id']))
        # Source components have no owner shortcut: an explicit cross-source
        # consumer eligibility link is followed irrespective of owner/source.
        pending = build.list()
        build.source_rules(pending, runtime.ctx.rules, source_ids)
        targets = build.list()
        for timer in runtime.clock.by_life[life['id']]:
            build.append(targets, timer['payload'])
        for hooks in life['hooks'].values():
            build.extend(targets, hooks)
        for target in targets:
            budget.pay(work=1)
            if target['kind'] == 'dispatch_rule':
                build.append(pending, target['ref_id'])
            else:
                linked = operations[target['ref_id']]
                build.append(seeds, linked)
                build.source_rules(pending, runtime.ctx.rules, build.set((linked['source_id'],)))
        for consumer in consumers.values():
            budget.pay(work=1)
            if consumer['pool_id'] == pool['id']:
                build.append(seeds, consumer)
                build.append(pending, consumer['eligibility_rule_id'])
        visited = build.set()
        while pending:
            budget.pay(work=1)
            rid = pending.pop()
            if rid in visited:
                continue
            build.add(visited, rid)
            row = runtime.ctx.rules[rid]
            build.append(seeds, row)
            gate_ref = row.get('resource_gate')
            if gate_ref is not None:
                consumer = consumers[gate_ref['consumer_id']]
                build.append(seeds, consumer)
                build.append(pending, consumer['eligibility_rule_id'])
        build.store(scopes, oid, seeds)
    runtime.compiler_reference_reservations = build.references
    index = DefinitionIndex(roots, spine_nodes=spine, scopes=scopes, budget=budget)
    return index


class IndexedQualification(QualificationService):
    """Private actual adapter only; original caller-fact API is unmodified."""
    def __init__(self, adapter):
        adapter.budget.pay(work=32+len(adapter.pool_runtime.config['operations'])+len(adapter.gates),
            storage=16384+512*len(adapter.pool_runtime.config['operations'])+1024*len(adapter.gates))
        self.adapter = adapter
        self.reserve = adapter.reaction.audit._charge
        self.work_limit = adapter.MEASUREMENT_WORK_LIMIT
        self.encoding_limit = 256*1024*1024
        self._usage = {'work': 0, 'encoding_bytes': 0, 'attempts': 0, 'permits': 0, 'published_bytes': 0}
        self._busy = False
        self._permits, self._attempts, self._index = {}, {}, {}
        self._operations = {row['id']: row for row in adapter.pool_runtime.config['operations']}
        self._program = adapter.index._fingerprint
        for oid, gate in adapter.gates.items():
            _exact(gate, ('operation_id', 'lifecycle_id', 'window', 'generation_policy'), 'gate')
            lid = _text(gate['lifecycle_id'], 'lifecycle')
            op = self._operations[oid]
            pool, life = adapter.pool_runtime.pools[op['pool_id']], adapter.clock.lives[lid]
            if (gate['operation_id'] != oid or pool['owner'] != life['owner']
                    or gate['window'] != 'established_until_end'
                    or gate['generation_policy'] != 'current_at_resource_attempt'):
                raise QualificationError('indexed_gate_binding')
            self._index[oid] = {'operation': op, 'pool_id': op['pool_id'], 'lifecycle_id': lid, 'owner': pool['owner']}

    def _operation(self, operation):
        self._charge()
        self.adapter.assert_indexed_operation(operation)
        oid = _text(operation.get('id'), 'operation')
        target = self._index.get(oid)
        if target is None or target['operation'] is not operation or self._operations.get(oid) is not operation:
            raise QualificationError('operation_identity')
        return oid, target
