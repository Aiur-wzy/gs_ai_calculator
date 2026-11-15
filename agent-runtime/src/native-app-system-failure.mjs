/** Host program faults are not Agent-fillable task issues or replay permission. */
const components = new Set(['python_engine_runtime', 'app_domain_tools', 'app_domain_binding'])
const causes = new Set(['module_unavailable', 'dependency_initialization_failed', 'internal_exception'])
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const keys = (value, expected) => record(value) && Object.keys(value).sort().join('|') === [...expected].sort().join('|')
function validCancellation(value) {
  return keys(value, ['code', 'reason', 'message', 'failure_kind', 'operation', 'retryable', 'details', 'recovery'])
    && value.code === 1 && value.reason === 'calculation_cancelled' && value.failure_kind === 'cancelled'
    && value.retryable === true && typeof value.message === 'string'
    && [...value.message].length > 0 && [...value.message].length <= 4096
    && typeof value.operation === 'string' && /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(value.operation)
    && keys(value.details, ['code', 'message', 'path', 'severity'])
    && value.details.code === 'calculation_cancelled' && value.details.message === value.message
    && value.details.path === 'execution' && value.details.severity === 'cancelled'
    && keys(value.recovery, ['action', 'retry_same_request'])
    && value.recovery.action === 'recalculate_fresh' && value.recovery.retry_same_request === false
}
export function validateAppSystemFailure(value) {
  if (!record(value) || !Object.hasOwn(value, 'failure_kind')) return false
  if (value.failure_kind === 'cancelled') {
    if (!validCancellation(value)) throw new TypeError('invalid_app_system_failure')
    return false // A valid cancellation is not a program fault or a successful result.
  }
  const fault = value.system_fault
  const storage = value.diagnostic_storage
  const storageFailed = keys(storage, ['state', 'reason']) && storage.state === 'failed'
    && storage.reason === 'diagnostic_persistence_failed'
  const storageValid = storage === undefined || keys(storage, ['state']) && storage.state === 'persisted' || storageFailed
  const valid = value.failure_kind === 'system' && value.code === 1 && value.retryable === false
    && !Object.hasOwn(value, 'category') && !Object.hasOwn(value, 'blocking_scope')
    && keys(fault, ['contract', 'component', 'cause_code', 'operation', 'diagnostic_id'])
    && fault.contract === 'genshin-app-system-failure-v1' && components.has(fault.component) && causes.has(fault.cause_code)
    && typeof fault.operation === 'string' && /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(fault.operation)
    && storageValid && (storageFailed ? fault.diagnostic_id === null : typeof fault.diagnostic_id === 'string'
      && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(fault.diagnostic_id))
    && value.reason === (fault.cause_code === 'internal_exception' ? 'app_internal_error' : 'app_component_unavailable')
    && typeof value.message === 'string'
    && keys(value.recovery, ['action', 'retry_same_request'])
    && value.recovery.action === 'report_system_fault' && value.recovery.retry_same_request === false
  if (!valid) throw new TypeError('invalid_app_system_failure')
  return true
}
