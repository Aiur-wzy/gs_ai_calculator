"""Opt-in, pure linear EM dependency graphs on immutable caller frames."""
from __future__ import annotations

import copy
import hashlib
import math

from genshin_engine import attribute_projection as projection
from genshin_engine.attribute_budget import (
    DEFAULT_LIMITS,
    attribute_budget_capabilities,
    budget_failure_details,
    limits_for_schema,
    normalize_attribute_budget,
)
from genshin_engine.attribute_ledger import (
    AttributeLedger,
    AttributeLedgerError,
    _canonical,
    _issue,
)
from genshin_engine.attribute_schema import (
    ATTRIBUTE_LEDGER_SCHEMA,
    BASIS,
    CONTRIBUTION,
    ID,
    LIMITS,
)
from genshin_engine.rotation_schema import arr, obj

GRAPH_CONTRACT = 'attribute-ledger-v2'
GRAPH_LIMITS = {key: DEFAULT_LIMITS[key] for key in ('dependency_depth', 'output_bytes')}
LINEAR = obj({'kind': {'const': 'linear_stat'},
              'input': obj({'actor': ID, 'stat': {'const': 'em'}, 'basis': {'enum': list(BASIS)}, 'frame_id': ID},
                           ['actor', 'stat', 'basis', 'frame_id']),
              'factor': {'type': 'number'}, 'add': {'type': 'number'}, 'min': {'type': 'number'}, 'max': {'type': 'number'}},
             ['kind', 'input', 'factor'])
GRAPH_CONTRIBUTION = copy.deepcopy(CONTRIBUTION)
GRAPH_CONTRIBUTION['properties']['value'] = {'oneOf': [{'type': 'number'}, LINEAR]}
GRAPH_CONTRIBUTION['properties'].update(output_domain={'enum': ['self', 'provider']},
                                        sample_binding=projection.SAMPLE_BINDING,
                                        projection_of=projection.PROJECTION_OF)
GRAPH_SCHEMA = copy.deepcopy(ATTRIBUTE_LEDGER_SCHEMA)
GRAPH_SCHEMA['properties']['contract'] = {'const': GRAPH_CONTRACT}
GRAPH_SCHEMA['properties']['contributions'] = arr(GRAPH_CONTRIBUTION)
GRAPH_SCHEMA['properties']['frames'] = arr(projection.GRAPH_FRAME)


def linear_value(raw, expression):
    """Finite intermediate arithmetic; a cap never hides overflow."""
    product = raw * expression['factor']
    value = math.fsum([product, expression.get('add', 0)])
    if not math.isfinite(product) or not math.isfinite(value):
        raise ArithmeticError('nonfinite linear expression')
    return min(expression.get('max', math.inf), max(expression.get('min', -math.inf), value))


def attribute_graph_capabilities():
    return {'contract': GRAPH_CONTRACT, 'schema': limits_for_schema(GRAPH_SCHEMA, DEFAULT_LIMITS),
            'limits': {**LIMITS, **GRAPH_LIMITS}, 'budget':attribute_budget_capabilities(), 'frame_authority': 'caller_supplied_frame',
            'runtime_integration': 'mechanics_explicit_attribute_model_v2', 'stats': ['em'], 'basis': list(BASIS),
            'expressions': 'one_attribute_input_linear_add_cap',
            'dependency_depth': 'longest_query_chain_including_cached_subgraphs',
            'historical_frame': 'same_frame_or_strictly_earlier_epoch_with_nonfuture_time_and_origin',
            'projection': 'matching_actor_total_root_only; dependencies_rejected_on_consumed_closure',
            'unsupported': ['damage_context', 'quota', 'resource_gate', 'hit_match', 'arbitrary_expressions', 'source_approval']}


class AttributeGraphLedger(AttributeLedger):
    contract = GRAPH_CONTRACT
    declaration_schema = GRAPH_SCHEMA
    contribution_schema = GRAPH_CONTRIBUTION
    frame_schema = projection.GRAPH_FRAME

    def __init__(self, declaration, *, limits=None):
        self._stack = []
        try:
            effective = normalize_attribute_budget(limits)['effective']
        except ValueError as exc:
            raise AttributeLedgerError('attribute_budget_invalid', str(exc), 'attribute_limits') from exc
        self._graph_limits = {key: effective[key] for key in GRAPH_LIMITS}
        self._output_bytes = 0
        super().__init__(declaration, limits=effective)

    @staticmethod
    def _validate_linear(node):
        expr = node['value']
        if not isinstance(expr, dict):
            return
        if any(not value.strip() or len(value) > 128 for value in expr['input'].values()):
            raise AttributeLedgerError('attribute_query', '转换输入标识必须是有界文本', node['id'])
        if expr.get('min', -math.inf) > expr.get('max', math.inf):
            raise AttributeLedgerError('attribute_expression_bounds', '转换下限不可大于上限', node['id'])

    def _validate_node(self, node):
        self._validate_linear(node)
        if 'sample_binding' in node and not isinstance(node['value'], dict):
            raise AttributeLedgerError('attribute_projection_source', 'event取样授权只适用于显式线性属性表达式', node['id'])

    def _validate_frame(self, frame):
        projection.validate_frame(frame)

    def _context_issue(self, query, frame):
        issue = projection.context_issue(query, frame, self._frames)
        if issue or projection.is_projection(frame):
            return issue
        return super()._context_issue(query, frame)

    def _node_qualification_issue(self, node, query):
        return projection.node_issue(node, query, self._frames, self._nodes)

    def _cacheable(self, result):
        # A dependency frame may be registered later. Known immutable closures
        # remain reusable; no pending parent result poisons that later read.
        return result['status'] != 'pending'

    def _receipt(self, query, status, rows, issues=(), *, value=None, baseline=None):
        result = super()._receipt(query, status, rows, issues, value=value, baseline=baseline)
        if status == 'known':
            result['dependency_depth'] = self._stack[-1]['depth'] if self._stack else 1
            result['receipt_id'] = hashlib.sha256(_canonical({k: v for k, v in result.items() if k != 'receipt_id'})).hexdigest()
        return result

    def _failure(self, code, message, query, *, dependency_path=None):
        issue = _issue(code, message, 'query', status='pending')
        if code == 'attribute_dependency_depth':
            issue.update(budget_failure_details('dependency_depth',len(self._stack),len(self._stack)+1,self._graph_limits['dependency_depth']))
        if dependency_path is not None:
            issue['dependency_path'] = dependency_path
        return self._receipt(query, 'pending', [], [issue])

    def read_stat(self, actor, stat, basis, frame_id, *, purpose='ordinary'):
        outer = not self._stack
        key = (actor, stat, basis, frame_id, purpose)
        if any(not isinstance(x, str) for x in key):
            result = super().read_stat(actor, stat, basis, frame_id, purpose=purpose)
            return self._export(result) if outer else result
        frame = self._frames.get(frame_id)
        query = {'actor': actor, 'stat': stat, 'basis': basis, 'purpose': purpose,
                 'frame': {k: v for k, v in frame.items() if k != 'contribution_ids'} if frame else {'id': frame_id}}
        path = [{'actor': item['key'][0], 'stat': item['key'][1], 'basis': item['key'][2], 'frame_id': item['key'][3],
                 **({'contribution_id': item['contribution_id']} if item.get('contribution_id') else {})} for item in self._stack]
        path.append({'actor': actor, 'stat': stat, 'basis': basis, 'frame_id': frame_id})
        if any(item['key'] == key for item in self._stack):
            result = (self._failure('attribute_dependency_cycle', '属性读取存在循环依赖，不能迭代猜测', query, dependency_path=path)
                      if self._charge_query(1) else self._failure('attribute_work_limit', '属性读取超过累计工作量限制', query))
        elif len(self._stack) >= self._graph_limits['dependency_depth']:
            result = (self._failure('attribute_dependency_depth', '属性依赖闭包超过最大链深度', query, dependency_path=path)
                      if self._charge_query(1) else self._failure('attribute_work_limit', '属性读取超过累计工作量限制', query))
        else:
            self._stack.append({'key': key, 'depth': 1})
            try:
                result = super().read_stat(actor, stat, basis, frame_id, purpose=purpose)
            finally:
                self._stack.pop()
        return self._export(result) if outer else result

    def _export(self, result):
        self._checkpoint()
        size = len(_canonical(result))
        if self._output_bytes + size > self._graph_limits['output_bytes']:
            issue = _issue('attribute_output_limit', '属性图累计外部输出字节超过限制', 'query', status='pending')
            issue.update(budget_failure_details('output_bytes',self._output_bytes,self._output_bytes+size,self._graph_limits['output_bytes']))
            return self._receipt(result['query'], 'pending', [], [issue])
        self._output_bytes += size
        return result

    def _linear_dependency(self, node, query):
        """One shared dependency qualification for numeric and structural reads."""
        expr = node['value']
        self._validate_linear(node)
        source, current = self._frames.get(expr['input']['frame_id']), query['frame']
        if source is not None:
            if projection.is_projection(source):
                return {'issue': _issue('attribute_projection_dependency', '出伤投影不是可共享普通属性，表达式不能读取它', node['id'], status='pending')}
            issue = projection.historical_issue(source, current, node['id'])
            if issue:
                return {'issue': issue}
        return {'input': expr['input']}

    def _node_value(self, node, query):
        expr = node['value']
        if not isinstance(expr, dict):
            return {'value': expr, 'metadata': {'evaluated_value': expr}}
        dependency = self._linear_dependency(node, query)
        if 'issue' in dependency:
            return dependency
        self._stack[-1]['contribution_id'] = node['id']
        ref = dependency['input']
        receipt = self.read_stat(ref['actor'], ref['stat'], ref['basis'], ref['frame_id'], purpose='ordinary')
        self._stack[-1].pop('contribution_id', None)
        if receipt['status'] != 'known':
            return {'issue': copy.deepcopy(receipt['issues'][0])}
        depth = 1 + receipt['dependency_depth']
        if depth > self._graph_limits['dependency_depth']:
            issue = _issue('attribute_dependency_depth', '属性依赖闭包超过最大链深度（包括缓存子图）', node['id'], status='pending')
            issue.update(budget_failure_details('dependency_depth',depth-1,depth,self._graph_limits['dependency_depth']))
            return {'issue': issue}
        self._stack[-1]['depth'] = max(self._stack[-1]['depth'], depth)
        try:
            value = linear_value(receipt['value'], expr)
        except ArithmeticError:
            return {'issue': _issue('attribute_value_invalid', '属性转换乘加溢出或不是有限值', node['id'])}
        return {'value': value, 'metadata': {'evaluated_value': value, 'input_receipt_id': receipt['receipt_id']}}

    def _node_dependencies(self, node, query):
        """Closed dependency list; defaults preserve v2's single input edge."""
        if not isinstance(node['value'], dict):
            return {'dependencies': []}
        dependency = self._linear_dependency(node, query)
        return dependency if 'issue' in dependency else {'dependencies': [dependency]}

    def dependency_footprint(self, actor, stat, basis, frame_id):
        """Qualified ordinary structure only; no numeric/source approval."""
        return self._dependency_footprint(actor, stat, basis, frame_id, root_purpose='ordinary')

    def _dependency_footprint(self, actor, stat, basis, frame_id, *, root_purpose):
        """Shared walker; only explicit subclasses expose projection roots."""
        raw = {'actor': actor, 'stat': stat, 'basis': basis, 'frame_id': frame_id}
        if root_purpose != 'ordinary':
            raw['purpose'] = root_purpose
        queries, edges, current, done, stack = {}, {}, set(), {}, []
        root = (actor, stat, basis, frame_id)

        def charge(amount=1):
            if not self._charge_query(amount):
                raise AttributeLedgerError('attribute_work_limit', '属性依赖扫描超过共享累计工作量', status='pending')

        def stop(issue):
            raise AttributeLedgerError(issue['code'], issue['message'], issue['path'], status=issue['severity'],
                **{k:v for k,v in issue.items() if k not in ('code','message','path','severity')})

        def ident(key):
            return 'query:' + hashlib.sha256(_canonical(key)).hexdigest()

        def visit(key):
            charge()
            if key in stack:
                raise AttributeLedgerError('attribute_dependency_cycle', '属性读取存在循环依赖，不能迭代猜测', 'query', status='pending')
            if len(stack) >= self._graph_limits['dependency_depth']:
                raise AttributeLedgerError('attribute_dependency_depth', '属性依赖闭包超过最大链深度', 'query', status='pending',
                    **budget_failure_details('dependency_depth',len(stack),len(stack)+1,self._graph_limits['dependency_depth']))
            if key in done:
                if len(stack) + done[key] > self._graph_limits['dependency_depth']:
                    raise AttributeLedgerError('attribute_dependency_depth', '属性依赖闭包超过最长链限制', 'query', status='pending',
                        **budget_failure_details('dependency_depth',len(stack),len(stack)+done[key],self._graph_limits['dependency_depth']))
                return done[key]
            a, s, b, fid = key
            frame = self._frames.get(fid)
            query = {'actor': a, 'stat': s, 'basis': b, 'purpose': root_purpose if key == root else 'ordinary',
                     'frame': {k: v for k, v in frame.items() if k != 'contribution_ids'} if frame else {'id': fid}}
            qid = ident(key)
            queries[qid] = {'id': qid, 'query': query, 'relation_to_root': 'current' if fid == frame_id else 'historical',
                            'status': 'pending'}
            stack.append(key)
            depth = 1
            membership = hashlib.sha256()
            try:
                for item in self._qualification_walk(query, frame):
                    material = {k: v for k, v in item.items() if k != 'node'}
                    encoded = _canonical(material)
                    charge(1 + len(encoded) // 1024)
                    membership.update(len(encoded).to_bytes(8, 'big'))
                    membership.update(encoded)
                    queries[qid]['qualified_membership_fingerprint'] = membership.hexdigest()
                    if item['kind'] == 'issue':
                        stop(item['issue'])
                    if item['kind'] != 'contribution':
                        continue
                    node = item['node']
                    dependencies = self._node_dependencies(node, query)
                    if 'issue' in dependencies:
                        stop(dependencies['issue'])
                    for dependency in dependencies['dependencies']:
                        ref = dependency['input']
                        child = (ref['actor'], ref['stat'], ref['basis'], ref['frame_id'])
                        edge = {'from': qid, 'to': ident(child), 'contribution_id': node['id'],
                                'component': copy.deepcopy(node['component']), 'instance_id': node['instance_id'], 'layer_id': node['layer_id'],
                                **({'role': dependency['role']} if 'role' in dependency else {})}
                        edge_id = hashlib.sha256(_canonical(edge)).hexdigest()
                        if edge_id not in edges:
                            charge()
                            edges[edge_id] = edge
                        depth = max(depth, 1 + visit(child))
                        if depth > self._graph_limits['dependency_depth']:
                            raise AttributeLedgerError('attribute_dependency_depth', '属性依赖闭包超过最长链限制', 'query', status='pending',
                                **budget_failure_details('dependency_depth',depth-1,depth,self._graph_limits['dependency_depth']))
            finally:
                stack.pop()
            queries[qid].update(status='ready', dependency_depth=depth)
            if fid == frame_id and b != 'base':
                current.add((a, s, b))
            done[key] = depth
            return depth

        issues = []
        try:
            charge()
            if any(not isinstance(v, str) or not v.strip() or len(v) > 128 for v in raw.values()):
                raise AttributeLedgerError('attribute_query', '属性查询标识必须是有界文本', 'query')
            visit(root)
        except AttributeLedgerError as error:
            if error.issue['code'] in ('attribute_work_limit', 'attribute_output_limit'):
                raise
            issues.append(error.issue)
        safe = {k: v if isinstance(v, str) and len(v) <= 128 else f'<{type(v).__name__}>' for k, v in raw.items()}
        result = {'contract': 'attribute-dependency-footprint-v1',
            'verification_scope': 'qualified_dependency_structure_no_numeric_or_source_authority',
            'status': issues[0]['severity'] if issues else 'ready',
            'query': copy.deepcopy(queries.get(ident(root), {}).get('query', safe)) if not issues else safe,
            'queries': [queries[key] for key in sorted(queries)],
            'edges': [edges[key] for key in sorted(edges)], 'issues': issues}
        if not issues:
            result['current_dependencies'] = [{'actor': a, 'stat': s, 'basis': b} for a, s, b in sorted(current)]
        else:
            result['prefix_only'] = True
        result['receipt_id'] = hashlib.sha256(_canonical(result)).hexdigest()
        size = len(_canonical(result))
        if self._output_bytes + size > self._graph_limits['output_bytes']:
            raise AttributeLedgerError('attribute_output_limit', '属性依赖扫描超过共享输出预算', status='pending',
                **budget_failure_details('output_bytes',self._output_bytes,self._output_bytes+size,self._graph_limits['output_bytes']))
        self._output_bytes += size
        return copy.deepcopy(result)

    def receipt_catalog(self):
        catalog = list(self._published.values())
        size = len(_canonical(catalog))
        if self._output_bytes + size > self._graph_limits['output_bytes']:
            raise AttributeLedgerError('attribute_output_limit', '属性图累计外部输出字节超过限制', status='pending',
                **budget_failure_details('output_bytes',self._output_bytes,self._output_bytes+size,self._graph_limits['output_bytes']))
        self._output_bytes += size
        return copy.deepcopy(catalog)

    def usage(self):
        return {**super().usage(), 'output_bytes': self._output_bytes, 'graph_limits': dict(self._graph_limits)}
