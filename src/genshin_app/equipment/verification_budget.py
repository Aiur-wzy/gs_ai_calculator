"""Instance-local replay budgets, independent of the artifact wire ceiling."""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES, MIB

POLICY = 'execution-verification-budget-v2'
DEFAULT_LIMITS = MappingProxyType({
    'requests': 64, 'executions': 4, 'input_bytes': 32 * MIB,
    'retained_input_bytes': 64 * MIB, 'output_bytes': 256 * MIB,
    'retained_output_bytes': 512 * MIB, 'returned_bytes': 2048 * MIB,
    'material_bytes': 288 * MIB, 'work': 256_000_000,
    'input_nodes': 1_000_000, 'output_nodes': 16_000_000,
    'depth': 64, 'string_chars': 65536,
})
HARD_LIMITS = MappingProxyType({key: value * (2 if key.endswith('bytes') else 4)
    for key, value in DEFAULT_LIMITS.items()})


def normalize_verification_budget(requested=None):
    if requested is None:
        requested = {'policy': POLICY, 'limits': {}}
    if (type(requested) is not dict or set(requested) != {'policy', 'limits'}
            or requested['policy'] != POLICY or type(requested['limits']) is not dict
            or set(requested['limits']) - DEFAULT_LIMITS.keys()):
        raise ValueError('核验预算须为当前版本及已知维度的实例请求。')
    effective = dict(DEFAULT_LIMITS)
    for key, value in requested['limits'].items():
        if type(value) is not int or not 0 <= value <= HARD_LIMITS[key]:
            raise ValueError('核验预算须为有限非负整数且不超过该策略硬界：' + key)
        effective[key] = value
    body = {'policy': POLICY, 'requested': {'policy': POLICY, 'limits': dict(requested['limits'])},
            'effective': effective}
    raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf8')
    return {**body, 'policy_fingerprint': hashlib.sha256(raw).hexdigest()}


def verification_budget_contract():
    return {'policy': POLICY, 'defaults': dict(DEFAULT_LIMITS), 'hard_limits': dict(HARD_LIMITS),
            'selection': 'trusted_session_constructor_only_no_document_owner_or_cache',
            'roles': {'output_bytes': 'one complete internal raw numerical replay, including repeated evidence',
                'retained_output_bytes': 'currently cached complete raw numerical replay bytes',
                'returned_bytes': 'cumulative successfully returned detached copies',
                'work': 'all visits and encoding/copy work, including failed attempts'},
            'wire_contract': f'independent_calculated_{CALCULATED_ARTIFACT_BYTES // MIB}MiB_and_RPC_limits_unchanged',
            'source_authority': 'none'}
