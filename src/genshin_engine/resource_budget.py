"""Instance resource audit policy; independent of output file envelopes."""
from __future__ import annotations

import copy
import hashlib
import json

CONTRACT = 'resource-execution-budget-v1'
# Preserve the former lifecycle adapter's effective 20m allowance explicitly.
DEFAULTS = {'work': 20_000_000, 'slots': 20000, 'bytes': 8 * 1024 * 1024,
            'terminal_bytes': 16384, 'position_work': 40000, 'position_bytes': 8 * 1024 * 1024,
            'index_storage': 64 * 1024 * 1024, 'index_encoding': 64 * 1024 * 1024,
            'index_graph_bytes': 8 * 1024 * 1024, 'index_scope_references': 100000,
            'index_containers': 30000, 'index_edges': 200000}
HARD_LIMITS = {k: v * 8 for k, v in DEFAULTS.items()}
HARD_LIMITS.update(work=64_000_000, bytes=32 * 1024 * 1024, position_bytes=32 * 1024 * 1024)
HARD_LIMITS.update(index_storage=256 * 1024 * 1024, index_encoding=256 * 1024 * 1024,
                   index_graph_bytes=8 * 1024 * 1024)
RESOURCE_LIMITS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    k: {'type': 'integer', 'minimum': 16384 if k == 'terminal_bytes' else 1, 'maximum': v} for k, v in HARD_LIMITS.items()}}


def normalize_resource_budget(requested=None):
    requested = {} if requested is None else requested
    if type(requested) is not dict or any(k not in HARD_LIMITS or type(v) is not int
            or not (16384 if k == 'terminal_bytes' else 1) <= v <= HARD_LIMITS[k] for k, v in requested.items()):
        raise ValueError('Resource limits require published positive integer dimensions')
    result = {'contract': CONTRACT, 'requested': copy.deepcopy(requested), 'effective': {**DEFAULTS, **requested}}
    result['policy_fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def validate_saved_resource_budget(policy):
    if (type(policy) is not dict or policy.get('contract') != CONTRACT
            or set(policy) != {'contract', 'requested', 'effective', 'policy_fingerprint'}
            or normalize_resource_budget(policy['requested']) != policy):
        raise ValueError('Unknown or inconsistent saved resource budget policy; no default substitution')
    return policy
