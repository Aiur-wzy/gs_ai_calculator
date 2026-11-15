"""Versioned per-execution attribute limits; never mutate process-wide policy."""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.resource_budget import RESOURCE_LIMITS_SCHEMA

POLICY_CONTRACT = 'attribute-budget-policy-v1'
BINDING_CONTRACT = 'attribute-execution-budget-v1'
NOT_APPLICABLE_CONTRACT = 'attribute-execution-budget-not-applicable-v1'
PRODUCER_CONTRACT = 'genshin-calculation-result-v2'
DEFAULT_LIMITS = {
    'initials': 128, 'contributions': 65536, 'frames': 16384,
    'declaration_bytes': 16 * 1024 * 1024, 'query_work': 1048576,
    'declaration_depth': 64, 'registration_work': 1048576,
    'execution_work': 1048576, 'receipt_bytes': 32 * 1024 * 1024,
    'dependency_depth': 64, 'output_bytes': 32 * 1024 * 1024,
}
HARD_LIMITS = {key: value * 8 for key, value in DEFAULT_LIMITS.items()}
HARD_LIMITS.update(declaration_depth=128, dependency_depth=128,
                   declaration_bytes=16 * 1024 * 1024,
                   receipt_bytes=32 * 1024 * 1024, output_bytes=32 * 1024 * 1024)
LIMITS_SCHEMA = {'type': 'object', 'properties': {
    key: {'type': 'integer', 'minimum': 1, 'maximum': maximum}
    for key, maximum in HARD_LIMITS.items()}, 'additionalProperties': False}
OPTIONS_SCHEMA = {'type': 'object', 'properties': {
    'schema_version': {'type': 'integer', 'const': 1}, 'attribute_limits': LIMITS_SCHEMA},
    'required': ['schema_version'], 'additionalProperties': False}
OPTIONS_SCHEMA['properties']['resource_limits'] = RESOURCE_LIMITS_SCHEMA


def budget_fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def normalize_attribute_budget(requested=None):
    """Strict sparse override; explicit small budgets are respected unchanged."""
    if requested is None:
        requested = {}
    if type(requested) is not dict:
        raise ValueError('属性执行限额必须是对象')
    for key, value in requested.items():
        if key not in HARD_LIMITS or type(value) is not int or not 1 <= value <= HARD_LIMITS[key]:
            raise ValueError(f'属性执行限额 {key} 必须是公开硬上限内的正整数')
    policy = {'contract': POLICY_CONTRACT, 'requested': copy.deepcopy(requested),
              'effective': {**DEFAULT_LIMITS, **requested}}
    policy['policy_fingerprint'] = budget_fingerprint(policy)
    return policy


def validate_calculation_options(value):
    if (type(value) is not dict or set(value)-{'schema_version','attribute_limits','resource_limits'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1):
        raise ValueError('计算执行配置需要schema_version=1及已发布字段')
    if 'attribute_limits' in value and type(value['attribute_limits']) is not dict:
        raise ValueError('属性执行限额必须是对象')
    if 'resource_limits' in value:
        from genshin_engine.resource_budget import normalize_resource_budget
        normalize_resource_budget(value['resource_limits'])
    return normalize_attribute_budget(value.get('attribute_limits', {}))


def limits_for_schema(schema, limits):
    """Locally publish attribute array bounds, keeping generic walkers bounded."""
    value = copy.deepcopy(schema)
    def visit(node, name=None):
        if isinstance(node, dict):
            if node.get('type') == 'array':
                bound = {'initials': limits['initials'], 'initial_basis': limits['initials'],
                         'frames': limits['frames'], 'contributions': limits['contributions'],
                         'effect_classifications': limits['contributions'],
                         'contribution_ids': limits['contributions']}.get(name)
                if bound is not None:
                    node['maxItems'] = bound
                    dimension = {'initial_basis':'initials','effect_classifications':'contributions',
                                 'contribution_ids':'contributions'}.get(name,name)
                    node['x-genshin-attribute-limit'] = {'dimension':dimension,'limit':bound}
            for key, item in node.items():
                if key == 'properties' and isinstance(item, dict):
                    for field, child in item.items():
                        visit(child, field)
                elif isinstance(item, (dict, list)):
                    visit(item, name)
        elif isinstance(node, list):
            for child in node:
                visit(child, name)
    visit(value)
    return value


def attribute_budget_capabilities():
    return {'contract': POLICY_CONTRACT, 'options_schema': copy.deepcopy(OPTIONS_SCHEMA),
            'defaults': dict(DEFAULT_LIMITS), 'hard_limits': dict(HARD_LIMITS),
            'scope': 'one_execution_cumulative; explicit lower limits are honored',
            'file_limits_are_independent': True,
            'native_path': 'calculation_options.attribute_limits',
            'pure_engine_path': 'execution_options.attribute_limits'}


def budget_failure_details(dimension, used, attempted, limit):
    value = {'resource': 'attribute_execution', 'dimension': dimension,
             'used': used, 'attempted': attempted, 'limit': limit,
             'policy_contract': POLICY_CONTRACT}
    maximum = HARD_LIMITS[dimension]
    if limit < maximum and attempted <= maximum:
        value['adjustment'] = {'path': '/calculation_options/attribute_limits/' + dimension,
                               'maximum': maximum, 'current': limit, 'minimum_required': attempted}
    return value


def declaration_capacity_issue(dimension, attempted, limit, path):
    """A finite declaration exceeds one actual, published execution budget."""
    return {'code':'attribute_declaration_limit','message':'属性声明超过本次执行限额，请调整已发布的预算后重新计算',
            'path':path,'severity':'pending', **budget_failure_details(dimension,0,attempted,limit)}


def attribute_array_capacity_issue(value, schema, path):
    """Interpret only the metadata our attribute schema adapter published."""
    spec = schema.get('x-genshin-attribute-limit')
    if (not isinstance(value,list) or not isinstance(spec,dict) or set(spec)!={'dimension','limit'}
            or spec['dimension'] not in ('initials','contributions','frames')
            or type(spec['limit']) is not int or not 1 <= spec['limit'] <= HARD_LIMITS[spec['dimension']]
            or schema.get('maxItems') != spec['limit'] or len(value) <= spec['limit']):
        return None
    return declaration_capacity_issue(spec['dimension'],len(value),spec['limit'],path)


def only_attribute_capacity_issues(issues):
    return bool(issues) and all(row.get('code')=='attribute_declaration_limit'
        and row.get('resource')=='attribute_execution' for row in issues)


def first_attribute_validation_issue(issues):
    """A real shape error takes precedence over an accompanying small budget."""
    return next((row for row in issues if row.get('code')!='attribute_declaration_limit'),issues[0])
