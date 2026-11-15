import assert from 'node:assert/strict'
import { validateAppSystemFailure } from './native-app-system-failure.mjs'

const value = { code: 1, reason: 'app_component_unavailable', message: '依赖组件不可用',
  failure_kind: 'system', retryable: false,
  system_fault: { contract: 'genshin-app-system-failure-v1', component: 'python_engine_runtime',
    cause_code: 'module_unavailable', operation: 'gs_read_context', diagnostic_id: 'a47a2090-670e-41ae-80a5-76dbd65cfe61' },
  recovery: { action: 'report_system_fault', retry_same_request: false } }
assert.equal(validateAppSystemFailure(value), true)
assert.equal(validateAppSystemFailure({ code: 1, category: 'request_error' }), false)
for (const patch of [
  { category: 'request_error' }, { blocking_scope: 'request' }, { retryable: true },
  { code: 'app_component_unavailable' }, { failure_kind: 'unknown' },
  { recovery: { action: 'correct_arguments', retry_same_request: false } },
  { system_fault: { ...value.system_fault, extra: 'not-in-contract' } },
  { system_fault: { ...value.system_fault, cause_code: 'invented' } },
]) assert.throws(() => validateAppSystemFailure({ ...value, ...patch }), /invalid_app_system_failure/)
console.log('native app system failure contract: passed')
