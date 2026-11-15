/** Pure signed App catalog; DSH owns tools and Host owns durable effects. */
import { createHash } from 'node:crypto'
import { canonicalEnvelopeJson } from './bridge-envelope.mjs'
import { compileNativeAppToolDefinitions, validateNativeAppToolSchema } from './native-app-tool-schema.mjs'
export const APP_TOOLS_SNAPSHOT_CONTRACT = 'genshin-native-app-tool-snapshot-v1'
export const MAX_SEQUENTIAL_APP_CALLS = 16_384
export const MAX_APP_TOOL_RESULT_BYTES = 4 * 1024 * 1024
export const MAX_APP_TOOL_SNAPSHOT_BYTES = 512 * 1024
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u
const HASH = /^[0-9a-f]{64}$/u
const TOOL_NAME = /^[a-z][a-z0-9_]{0,127}$/u
const SNAPSHOT_BODY_KEYS = new Set([
  'contract', 'workflow', 'workflow_stage', 'document_id',
  'candidate_revision', 'stage_revision', 'database_fingerprint',
  'tool_manifest_hash', 'runtime_generation', 'writer_fence',
  'max_app_calls', 'tools', 'policies',
])
const DEFINITION_KEYS = new Set(['type', 'name', 'description', 'inputSchema'])
const POLICY_KEYS = new Set(['policy', 'effect'])
const WEB_TOOLS = new Set(['web_search', 'web_fetch'])
const REQUIRED_FIXED_TOOLS = new Set([
  'gs_list_research_calls',
  'gs_report_research_usage',
  'gs_app_get_workspace',
  'gs_app_request_action',
])
const EFFECTS = new Set([
  'none',
  'session_resource',
  'finding',
  'candidate',
  'candidate_and_derived',
  'derived_compose',
  'derived_calculation',
  'review_projection',
  'derived_validation',
  'stage_transition',
  'external_research_ledger',
  'external_action_ledger',
])
const POLICIES = new Set([
  'domain_read',
  'database_query',
  'database_read',
  'candidate_write',
  'derived_compute',
  'session_record_write',
  'stage_transition',
  'derived_validation',
  'research_ledger_read',
  'research_ledger_write',
  'app_workspace_read',
  'app_action_request',
])
const EXPECTED_TOOL_POLICIES = Object.freeze({
  gs_preview_attribute_recovery: { policy: 'domain_read', effect: 'none' },
  gs_hash_input: { policy: 'domain_read', effect: 'none' },
  gs_read_context: { policy: 'domain_read', effect: 'none' },
  gs_read_skill: { policy: 'domain_read', effect: 'none' },
  gs_query_context: { policy: 'database_query', effect: 'session_resource' },
  gs_query_runtime: { policy: 'database_query', effect: 'session_resource' },
  gs_query_appendix: { policy: 'database_query', effect: 'session_resource' },
  gs_list_appendix_categories: { policy: 'database_read', effect: 'none' },
  gs_get_character_appendix_index: { policy: 'database_query', effect: 'session_resource' },
  gs_patch_candidate: { policy: 'candidate_write', effect: 'candidate' },
  gs_patch_whiteboard: { policy: 'candidate_write', effect: 'candidate' },
  gs_add_character: { policy: 'candidate_write', effect: 'candidate' },
  gs_assemble_attack: { policy: 'candidate_write', effect: 'candidate' },
  gs_assemble_periodic: { policy: 'candidate_write', effect: 'candidate' },
  gs_refresh_data_refs: {
    policy: 'candidate_write', effect: 'candidate_and_derived',
  },
  gs_adopt_attributes: {
    policy: 'candidate_write', effect: 'candidate_and_derived',
  },
  gs_compose: { policy: 'derived_compute', effect: 'derived_compose' },
  gs_calculate: { policy: 'derived_compute', effect: 'derived_calculation' },
  gs_diagnose_result: { policy: 'domain_read', effect: 'none' },
  gs_review_result: { policy: 'domain_read', effect: 'none' },
  gs_review_result_batch: {
    policy: 'domain_read', effect: 'review_projection',
  },
  gs_record_finding: { policy: 'session_record_write', effect: 'finding' },
  gs_character_panel_basis: { policy: 'database_read', effect: 'none' },
  gs_weapon_stats: { policy: 'database_read', effect: 'none' },
  gs_reference_panel: { policy: 'database_read', effect: 'none' },
  gs_search_catalog: { policy: 'database_read', effect: 'none' },
  gs_transition_phase: { policy: 'stage_transition', effect: 'stage_transition' },
  gs_validate_topology: {
    policy: 'derived_validation', effect: 'derived_validation',
  },
  gs_validate_execution_bindings: {
    policy: 'derived_validation', effect: 'derived_validation',
  },
  gs_validate_agent_materialization: {
    policy: 'derived_validation', effect: 'derived_validation',
  },
  gs_verify_public_data_lookup: {
    policy: 'candidate_write', effect: 'candidate',
  },
  gs_attest_database_missing: {
    policy: 'candidate_write', effect: 'candidate',
  },
  gs_request_public_data_patch_approval: {
    policy: 'candidate_write', effect: 'candidate',
  },
  gs_list_research_calls: {
    policy: 'research_ledger_read', effect: 'external_research_ledger',
  },
  gs_report_research_usage: {
    policy: 'research_ledger_write', effect: 'external_research_ledger',
  },
  gs_app_get_workspace: {
    policy: 'app_workspace_read', effect: 'external_action_ledger',
  },
  gs_app_request_action: {
    policy: 'app_action_request', effect: 'external_action_ledger',
  },
})

export class NativeAppToolCatalogError extends Error {
  constructor(code, message = code, details = undefined) {
    super(message)
    this.name = 'NativeAppToolCatalogError'
    this.code = code
    this.details = details
  }
}

function reject(code, message = code, details = undefined) {
  throw new NativeAppToolCatalogError(code, message, details)
}

function exactKeys(value, expected, code) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) reject(code)
  let actual
  try {
    actual = Reflect.ownKeys(value)
  } catch {
    reject(code)
  }
  if (actual.some(key => typeof key !== 'string')
      || actual.length !== expected.size
      || actual.some(key => !expected.has(key))) reject(code)
}

function plainRecord(value, code) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) reject(code)
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) reject(code)
  return value
}

function identity(value, field) {
  if (typeof value !== 'string' || !IDENTITY.test(value)) {
    reject(`invalid_${field}`, `${field} is outside the bridge identity grammar`)
  }
  return value
}

function digest(value, field) {
  if (typeof value !== 'string' || !HASH.test(value)) {
    reject(`invalid_${field}`, `${field} must be a lowercase SHA-256 digest`)
  }
  return value
}

function revision(value, field) {
  if (!Number.isSafeInteger(value) || value < 0) {
    reject(`invalid_${field}`, `${field} must be a non-negative safe integer`)
  }
  return value
}

function sha256Utf8(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex')
}

function snapshotBody(value) {
  exactKeys(value, new Set([...SNAPSHOT_BODY_KEYS, 'catalog_hash']), 'snapshot_shape')
  const body = {}
  for (const key of SNAPSHOT_BODY_KEYS) body[key] = value[key]
  return body
}

function readSnapshot(value) {
  const body = snapshotBody(value)
  if (body.contract !== APP_TOOLS_SNAPSHOT_CONTRACT) reject('snapshot_contract')
  for (const field of [
    'workflow', 'workflow_stage', 'document_id', 'runtime_generation', 'writer_fence',
  ]) identity(body[field], field)
  for (const field of ['database_fingerprint', 'tool_manifest_hash']) {
    digest(body[field], field)
  }
  revision(body.candidate_revision, 'candidate_revision')
  revision(body.stage_revision, 'stage_revision')
  if (body.max_app_calls !== MAX_SEQUENTIAL_APP_CALLS) reject('snapshot_call_limit')
  if (!Array.isArray(body.tools) || body.tools.length < 1 || body.tools.length > 64) {
    reject('snapshot_tool_count')
  }
  const names = []
  for (const definition of body.tools) {
    exactKeys(definition, DEFINITION_KEYS, 'snapshot_tool_shape')
    if (definition.type !== 'function'
        || typeof definition.name !== 'string' || !TOOL_NAME.test(definition.name)
        || typeof definition.description !== 'string'
        || definition.description.length < 1 || definition.description.length > 16_384
        || definition.inputSchema === null
        || typeof definition.inputSchema !== 'object'
        || Array.isArray(definition.inputSchema)) {
      reject('snapshot_tool_definition')
    }
    if (WEB_TOOLS.has(definition.name)) reject('snapshot_web_tool_forbidden')
    names.push(definition.name)
  }
  if (JSON.stringify(names) !== JSON.stringify([...new Set(names)].sort())) {
    reject('snapshot_tool_order')
  }
  if ([...REQUIRED_FIXED_TOOLS].some(name => !names.includes(name))) {
    reject('snapshot_fixed_tools_missing')
  }
  plainRecord(body.policies, 'snapshot_policies')
  if (JSON.stringify(Object.keys(body.policies).sort()) !== JSON.stringify(names)) {
    reject('snapshot_policies')
  }
  for (const name of names) {
    const policy = body.policies[name]
    exactKeys(policy, POLICY_KEYS, 'snapshot_policy_shape')
    identity(policy.policy, 'policy')
    identity(policy.effect, 'effect')
    if (!POLICIES.has(policy.policy) || !EFFECTS.has(policy.effect)) {
      reject('snapshot_policy_unsupported')
    }
    const expected = EXPECTED_TOOL_POLICIES[name]
    if (expected === undefined
        || expected.policy !== policy.policy || expected.effect !== policy.effect) {
      reject('snapshot_policy_mismatch')
    }
  }
  let encoded
  try {
    encoded = canonicalEnvelopeJson(body)
  } catch {
    reject('snapshot_json')
  }
  if (Buffer.byteLength(encoded, 'utf8') > MAX_APP_TOOL_SNAPSHOT_BYTES) {
    reject('snapshot_size')
  }
  const actualHash = sha256Utf8(encoded)
  if (digest(value.catalog_hash, 'catalog_hash') !== actualHash) {
    reject('snapshot_hash_mismatch')
  }

  // Public schema compilation is stateless; do not instantiate a legacy
  // dispatcher or its frozen Turn/stage settlement machinery to validate it.
  for (const definition of body.tools) validateNativeAppToolSchema(definition)
  return { body, catalogHash: actualHash }
}

export function hashAppToolsSnapshotBody(body) {
  exactKeys(body, SNAPSHOT_BODY_KEYS, 'snapshot_body_shape')
  let encoded
  try {
    encoded = canonicalEnvelopeJson(body)
  } catch {
    reject('snapshot_json')
  }
  return sha256Utf8(encoded)
}

export function validateAppToolsSnapshot(value) {
  const checked = readSnapshot(value)
  return structuredClone({ ...checked.body, catalog_hash: checked.catalogHash })
}

export function compileAppToolDefinitions(snapshot, invoke) {
  if (typeof invoke !== 'function') reject('tool_invoke_required')
  const checked = readSnapshot(snapshot)
  const safePolicies = new Set(['domain_read', 'database_read', 'research_ledger_read', 'app_workspace_read'])
  return Object.freeze(compileNativeAppToolDefinitions(checked.body.tools,
    (definition, args, exec) => invoke(definition.name, args, exec)).map(definition => Object.freeze({
      ...definition,
      isConcurrencySafe: () => {
        const policy = checked.body.policies[definition.name]
        return safePolicies.has(policy.policy) && (policy.effect === 'none'
          || policy.policy === 'research_ledger_read' || policy.policy === 'app_workspace_read')
      },
    })))
}
