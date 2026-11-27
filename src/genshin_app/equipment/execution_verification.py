"""One-operation, private replay sharing; never a source approval service."""
from __future__ import annotations

import copy
import json
import math
import threading
from types import MappingProxyType

from .verification_budget import DEFAULT_LIMITS, normalize_verification_budget
from .verification_materials import walk_material

# Read-only compatibility discovery. Running sessions never consult this alias.
LIMITS = DEFAULT_LIMITS
KEYS = frozenset({'template', 'rotation_model', 'mechanics_model', 'matches'})


class ExecutionVerificationError(ValueError):
    def __init__(self, code, message, *, path='execution_verification', details=None):
        super().__init__(message)
        self.code = self.reason = code
        self.path = path
        self.details = details or {'code': code, 'path': path}


def verification_diagnostic(error):
    """Recover a typed capacity failure through existing ValueError wrappers."""
    seen = set()
    for _ in range(32):
        if error is None or id(error) in seen:
            return None
        seen.add(id(error))
        details = getattr(error, 'verification_diagnostic', None) or getattr(error, 'details', None)
        if (isinstance(details, dict)
                and details.get('contract') == 'execution-verification-diagnostic-v1'):
            return copy.deepcopy(details)
        error = error.__cause__ or error.__context__
    return None


def annotate_verification_failure(error):
    """Keep domain-specific pending facts while exposing bounded diagnostics."""
    diagnostic = verification_diagnostic(error)
    if diagnostic is not None:
        error.verification_diagnostic = diagnostic
        if not getattr(error, 'reason', None):
            error.reason = diagnostic['issues'][0]['code']
        if not getattr(error, 'path', None):
            error.path = diagnostic['issues'][0]['path']
        old = getattr(error, 'details', None)
        if not old or isinstance(old, dict) and old.get('contract') == diagnostic['contract']:
            error.details = diagnostic
        elif isinstance(old, dict):
            error.details = {**old, 'verification': diagnostic}


class ExecutionVerificationSession:
    """No JSON registration, supplied evaluator, ambient cache or output seed."""
    def __init__(self, *, budget=None):
        self._policy = normalize_verification_budget(budget)
        self.limits = MappingProxyType(dict(self._policy['effective']))
        self._owner = threading.current_thread()
        self._busy = self._closed = self._stopped = False
        self._cache = []
        self._verified_windows = set()
        self._replay_special_catalog = None
        self.special_capability_proof_fingerprint = None
        self._last_failure = None
        self._usage = {key: 0 for key in ('requests', 'executions', 'cache_hits', 'work',
            'retained_input_bytes', 'retained_output_bytes', 'returned_bytes',
            'peak_retained_input_bytes', 'peak_retained_output_bytes',
            'scanned_input_bytes', 'scanned_output_bytes', 'scanned_input_nodes', 'scanned_output_nodes',
            'maximum_input_bytes', 'maximum_output_bytes', 'maximum_input_nodes', 'maximum_output_nodes')}

    def _check(self):
        if threading.current_thread() is not self._owner:
            raise ExecutionVerificationError('verification_thread', '核验会话只能由创建它的操作线程使用。')
        if self._closed or self._stopped:
            raise ExecutionVerificationError('verification_stopped', '核验会话已经结束或耗尽预算。', details=self._last_failure)
        if self._busy:
            raise ExecutionVerificationError('verification_reentry', '核验会话不能在求值期间重入。')

    def _fail(self, code, message, *, path='execution_verification', dimension=None,
              used=None, attempted=None, limit=None, role=None):
        issue = {'code': code, 'message': message, 'path': path, 'severity': 'pending',
                 'dimension': dimension, 'used': used, 'attempted': attempted, 'limit': limit, 'role': role}
        self._last_failure = {'contract': 'execution-verification-diagnostic-v1', 'sealed': False,
            'accepted_result': False, 'status': 'verification_incomplete', 'issues': [issue],
            'budget': copy.deepcopy(self._policy), 'usage': copy.deepcopy(self._usage),
            'recovery': '保留原生方案；调整明确的内部容量后fresh重算，不修改来源、事件或旧结果回执。'}
        raise ExecutionVerificationError(code, message, path=path, details=copy.deepcopy(self._last_failure))

    def _capacity(self, field, count, *, path='execution_verification'):
        if self._usage[field] + count > self.limits[field]:
            self._stopped = True
            self._fail('verification_budget', '本次操作的核验预算不足；未形成接纳结果。',
                path=path, dimension=field, used=self._usage[field],
                attempted=self._usage[field] + count, limit=self.limits[field])

    def _reserve(self, field, count, *, path='execution_verification'):
        self._capacity(field, count, path=path)
        self._usage[field] += count

    def _observe(self, role, size, nodes):
        for unit, value in (('bytes', size), ('nodes', nodes)):
            self._usage['scanned_' + role + '_' + unit] += value
            key = 'maximum_' + role + '_' + unit
            self._usage[key] = max(self._usage[key], value)

    def _walk(self, value, *, role, clone=True, envelope=False, add_material_bytes=None, subtree_metrics=None):
        work = self._usage['work']
        work_limit = self.limits['work']

        def reserve_work(count, path):
            nonlocal work
            attempted = work + count
            if attempted > work_limit:
                self._usage['work'] = work
                self._stopped = True
                self._fail('verification_budget', '本次操作的核验预算不足；未形成接纳结果。',
                    path=path, dimension='work', used=work, attempted=attempted, limit=work_limit)
            work = attempted

        def failure(code, message, **details):
            self._usage['work'] = work
            if not clone:
                dimension = details.get('dimension')
                code = ('verification_' + role + '_bytes' if code == 'verification_size' else
                        'verification_' + role + '_nodes' if dimension == role + '_nodes' else
                        'verification_material_depth' if dimension == 'depth' else 'verification_material_shape')
            self._fail(code, message, **details)
        try:
            return walk_material(value, role=role, limits=self.limits,
                reserve_work=reserve_work,
                fail=failure, clone=clone, envelope=envelope,
                add_material_bytes=add_material_bytes, observe=self._observe, subtree_metrics=subtree_metrics)
        except ExecutionVerificationError as error:
            # The walker finally records even its partial failed scan.
            self._usage['work'] = work
            if self._last_failure is not None:
                self._last_failure['usage'] = copy.deepcopy(self._usage)
                error.details = copy.deepcopy(self._last_failure)
            raise
        finally:
            self._usage['work'] = work

    def _returned_copy(self, stored, output_size, nodes):
        # Check before allocation, count only successfully returned bytes.
        for unit, count in (('bytes', output_size), ('nodes', nodes)):
            if count > self.limits['output_' + unit]:
                self._fail('verification_size' if unit == 'bytes' else 'verification_shape',
                    '核验投影超过单项输出限制。', path='/output', dimension='output_' + unit,
                    used=0, attempted=count, limit=self.limits['output_' + unit], role='output')
        self._capacity('returned_bytes', output_size, path='/output')
        # Only private, detached, previously validated engine output reaches
        # here. It cannot be mutated by the caller or seeded from a document.
        self._reserve('work', nodes + math.ceil(output_size / 256), path='/output')
        detached = copy.deepcopy(stored)
        self._usage['returned_bytes'] += output_size
        return detached

    def bind_replay_capability_catalog(self, catalog, *, proof_fingerprint):
        """Retain an already verified source within this replay operation."""
        self._check()
        self._replay_special_catalog = copy.deepcopy(catalog)
        self.special_capability_proof_fingerprint = proof_fingerprint

    def _execution_input(self, execution, *, capability_hash=None):
        if type(execution) is not dict or set(execution) != KEYS:
            self._fail('verification_input', '核验需要完整模板、动作、最终机制和收益关联。')
        detached = self._walk(execution, role='input')[0]
        from .energy_policy import require_replay_energy_policy
        require_replay_energy_policy(detached)
        raw = json.dumps(detached, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')
        if capability_hash is not None:
            raw += b"\0" + capability_hash.encode("ascii")
        return detached, raw

    def _retain_execution(self, raw, actual):
        if isinstance(actual, dict) and isinstance(actual.get('result'), dict):
            from .replay_numeric_projection import numeric_budget_result
            actual = {**actual, 'result': numeric_budget_result(actual['result'])}
        metrics = {}
        stored, output_size, _ = self._walk(actual, role='output', subtree_metrics=metrics)
        self._capacity('retained_output_bytes', output_size, path='/output')
        self._cache.append((raw, stored, output_size, metrics))
        self._usage['retained_input_bytes'] += len(raw)
        self._usage['retained_output_bytes'] += output_size
        for role in ('input', 'output'):
            key = 'retained_' + role + '_bytes'
            self._usage['peak_' + key] = max(self._usage['peak_' + key], self._usage[key])
        return stored, output_size, metrics

    def execute_fresh(self, execution):
        """Run the engine in this operation and retain independent evidence for its seal."""
        self._check()
        self._busy = True
        try:
            from genshin_engine.special_reaction_capabilities import active_special_reaction_catalog_hash
            detached, raw = self._execution_input(execution, capability_hash=active_special_reaction_catalog_hash())
            self._capacity('retained_input_bytes', len(raw), path='/input')
            from genshin_engine.mechanics import execute_mechanics
            from genshin_runtime.operation_timing import timed_call
            actual = timed_call("engine_execute", execute_mechanics, **detached)
            if actual.get('resolved_template') is not None and actual.get('result') is not None:
                # Copy and meter engine output before it leaves this method and
                # can be annotated by the caller. There is no external seed.
                self._retain_execution(raw, actual)
            return actual
        finally:
            self._busy = False

    def _replay_result(self, stored, output_size, metrics, result_fields, response_fields):
        if result_fields is None or not isinstance(stored, dict) or not isinstance(stored.get('result'), dict):
            return self._returned_copy(stored, output_size, metrics['/output'][1])
        projected = {
            'resolved_template': stored.get('resolved_template'),
            'result': {key: stored['result'][key] for key in result_fields if key in stored['result']},
            **{key: stored[key] for key in response_fields if key in stored},
        }
        # All selected values are private, already checked subtrees. Reuse
        # their exact byte/node measurements instead of walking a large trace
        # again. Only the newly constructed dictionary envelopes are counted.
        def escaped(key):
            return key.replace('~', '~0').replace('/', '~1')

        def object_size(fields, prefix):
            size, nodes = 2 + max(0, len(fields) - 1), 1
            for key in fields:
                size += len(json.dumps(key, ensure_ascii=False).encode('utf8')) + 1
                metric_key = prefix + '/' + escaped(key)
                if key == 'resolved_template' and prefix == '/output' and key not in stored:
                    field_size, field_nodes = 4, 1  # The one explicit new null, not missing evidence.
                elif metric_key not in metrics:
                    self._fail('verification_projection_metrics', '内部核验投影缺少已验证的子树计量。',
                               path=metric_key[:1024], role='output')
                else:
                    field_size, field_nodes = metrics[metric_key]
                size += field_size
                nodes += 1 + field_nodes
            return size, nodes

        fields = [key for key in projected if key != 'result']
        size, nodes = object_size(fields, '/output')
        child_size, child_nodes = object_size(projected['result'], '/output/result')
        size += (1 if fields else 0) + len('"result":') + child_size
        nodes += 1 + child_nodes
        return self._returned_copy(projected, size, nodes)

    def replay(self, execution, *, _result_fields=None, _response_fields=()):
        self._check()
        self._reserve('requests', 1)
        self._busy = True
        try:
            from genshin_engine.special_reaction_capabilities import use_special_reaction_capability_catalog
            catalog = self._replay_special_catalog
            detached, raw = self._execution_input(execution, capability_hash=catalog["content_hash"] if catalog else None)
            for old_raw, stored, output_size, metrics in self._cache:
                self._reserve('work', math.ceil(min(len(old_raw), len(raw)) / 256) + 1)
                if old_raw == raw:
                    self._usage['cache_hits'] += 1
                    return self._replay_result(stored, output_size, metrics, _result_fields, _response_fields)
            self._reserve('executions', 1)
            self._capacity('retained_input_bytes', len(raw), path='/input')
            from genshin_engine.mechanics import execute_mechanics
            with use_special_reaction_capability_catalog(catalog):
                actual = execute_mechanics(**detached)
            stored, output_size, metrics = self._retain_execution(raw, actual)
            return self._replay_result(stored, output_size, metrics, _result_fields, _response_fields)
        finally:
            self._busy = False

    def scan_receipt_materials(self, *, inputs=(), outputs=()):
        """Aggregate finite input/output roles without seeding replay."""
        self._check()
        self._busy = True
        try:
            if type(inputs) is not tuple or type(outputs) is not tuple:
                self._fail('verification_material_roles', '核验材料角色必须由内部调用方指定。')
            total_size = 0
            def add_bytes(amount, path):
                nonlocal total_size
                total_size += amount
                if total_size > self.limits['material_bytes']:
                    self._fail('verification_material_bytes', '核验材料合计超过实例字节限制。',
                        path=path, dimension='material_bytes', used=total_size - amount,
                        attempted=total_size, limit=self.limits['material_bytes'])
            totals = {}
            for role, values in (('input', inputs), ('output', outputs)):
                _, size, nodes = self._walk(values, role=role, clone=False, envelope=True, add_material_bytes=add_bytes)
                totals[role] = {'nodes': nodes, 'bytes': size}
            return {**totals, 'total_bytes': total_size}
        finally:
            self._busy = False

    def report(self):
        if threading.current_thread() is not self._owner or self._busy:
            raise ExecutionVerificationError('verification_reentry', '核验期间不能跨线程或重入读取会话。')
        return {'scope': 'same_operation_execution_replay_only_no_source_authority',
            'closed': self._closed, 'stopped': self._stopped, 'budget': copy.deepcopy(self._policy),
            'usage': copy.deepcopy(self._usage), 'last_failure': copy.deepcopy(self._last_failure)}

    def close(self):
        if threading.current_thread() is not self._owner or self._busy:
            raise ExecutionVerificationError('verification_reentry', '核验期间不能跨线程或重入关闭会话。')
        self._cache.clear()
        self._replay_special_catalog = None
        self.special_capability_proof_fingerprint = None
        self._verified_windows.clear()
        self._usage['retained_input_bytes'] = self._usage['retained_output_bytes'] = 0
        self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *exc):
        self.close()
