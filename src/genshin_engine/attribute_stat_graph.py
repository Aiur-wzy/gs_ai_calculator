"""Opt-in ordinary multi-stat graph reusing the immutable bounded ledger."""
from __future__ import annotations

import copy
import math

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError, _issue
from genshin_engine.attribute_schema import FRAME
from genshin_engine.attribute_stat_schema import (
    STAT_CONTRIBUTION,
    STAT_GRAPH_CONTRACT,
    STAT_GRAPH_SCHEMA,
)


class AttributeStatGraphLedger(AttributeGraphLedger):
    contract = STAT_GRAPH_CONTRACT
    declaration_schema = STAT_GRAPH_SCHEMA
    contribution_schema = STAT_CONTRIBUTION
    frame_schema = FRAME

    def _query_support_issue(self, query):
        supported = {'em': ('total', 'nonextra', 'base'), 'atk': ('total', 'base')}
        if query['basis'] not in supported.get(query['stat'], ()):
            return _issue('attribute_query_unsupported', 'v3纯图仅支持EM三基底与ATK total/base', 'query', status='pending')

    def _validate_node(self, node):
        super()._validate_node(node)
        percentage = node['application']['kind'] == 'recipient_base_fraction'
        if ((node['value_unit'] == 'ratio') != percentage
                or percentage and node['stat'] != 'atk'
                or node['stat'] == 'atk' and node['nonextra_inclusion'] != 'unknown'):
            raise AttributeLedgerError('attribute_unit_scope', '属性输出量纲、基础比例应用或NONEXTRA分类不在本合同范围', node['id'])

    @staticmethod
    def _application_base(node, query):
        return {'actor': node['component']['recipient'], 'stat': node['stat'],
                'basis': 'base', 'frame_id': query['frame']['id']}

    def _node_dependencies(self, node, query):
        result = super()._node_dependencies(node, query)
        if 'issue' in result:
            return result
        dependencies = [{**row, 'role': 'value_input'} for row in result['dependencies']]
        if node['application']['kind'] == 'recipient_base_fraction':
            dependencies.append({'input': self._application_base(node, query), 'role': 'application_base'})
        return {'dependencies': dependencies}

    def _node_value(self, node, query):
        evaluated = super()._node_value(node, query)
        if 'issue' in evaluated or node['application']['kind'] == 'flat':
            return evaluated
        ref = self._application_base(node, query)
        self._stack[-1]['contribution_id'] = node['id']
        try:
            base = self.read_stat(ref['actor'], ref['stat'], ref['basis'], ref['frame_id'])
        finally:
            self._stack[-1].pop('contribution_id', None)
        if base['status'] != 'known':
            return {'issue': copy.deepcopy(base['issues'][0])}
        depth = 1 + base['dependency_depth']
        if depth > self._graph_limits['dependency_depth']:
            return {'issue': _issue('attribute_dependency_depth', '基础比例应用超过最大依赖深度', node['id'], status='pending')}
        self._stack[-1]['depth'] = max(self._stack[-1]['depth'], depth)
        value = evaluated['value'] * base['value']
        if not math.isfinite(value):
            return {'issue': _issue('attribute_value_invalid', '基础比例应用乘法溢出或不是有限值', node['id'])}
        return {'value': value, 'metadata': {**evaluated['metadata'],
                'raw_evaluated_value': evaluated['value'], 'application_base_value': base['value'],
                'application_base_receipt_id': base['receipt_id'], 'evaluated_value': value}}
