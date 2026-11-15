/** App-owned, sparse configuration for the reviewed DSH production modules. */
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { PRUNE_MARKER } from '@deepseek-ai/dsh-compaction-tool-result-pruner'

export const NATIVE_DSH_SETTINGS_CONTRACT = 'genshin-native-dsh-settings-v1'
export const NATIVE_DSH_SETTINGS_VERSION = 1
export const NATIVE_DSH_SETTINGS_FILE = 'native-agent-dsh-settings.json'
export const NATIVE_DSH_RELEASE = '0.2.1-rc.5'
export const NATIVE_DSH_REVIEWED_MODEL_IDS = Object.freeze([
  'deepseek-flash',
  'deepseek-v4-pro',
])

const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u
const REVIEWED_MODEL_IDS = new Set(NATIVE_DSH_REVIEWED_MODEL_IDS)
const SOURCE = /^[a-z][a-z0-9_-]{0,31}$/u
const SECTIONS = new Set([
  'compaction',
  'toolResultPruner',
  'agentLoop',
  'persistence',
  'sessionQuery',
  'projectionCache',
])

const COMPACTION_FIELDS = new Set([
  'thresholdRatio', 'headroomTokens', 'retainRatio', 'retainTokens',
  'summarizationProvider', 'summarizationModel', 'maxTokens',
  'compactionRetries', 'maxOverflowRetries', 'modelPolicies', 'auto',
])
const MODEL_POLICY_FIELDS = new Set([
  'provider', 'model', 'thresholdRatio', 'headroomTokens', 'retainRatio',
  'retainTokens', 'summarizationProvider', 'summarizationModel', 'maxTokens',
  'compactionRetries', 'maxOverflowRetries',
])

function deepFreeze(value, seen = new Set()) {
  if (value === null || typeof value !== 'object' || seen.has(value)) return value
  seen.add(value)
  for (const child of Object.values(value)) deepFreeze(child, seen)
  return Object.freeze(value)
}

// Resolved display defaults.  Plugin rows remain sparse and let DSH itself
// apply these values; this snapshot is for the App settings contract only.
// Projection cache deliberately has no upstream defaults, so its two required
// deployment values remain App-owned.
export const NATIVE_DSH_DEFAULTS = deepFreeze({
  compaction: {
    thresholdRatio: 0.8,
    headroomTokens: 65_536,
    retainRatio: 0.16,
    summarizationProvider: '',
    summarizationModel: '',
    compactionRetries: 1,
    maxOverflowRetries: 1,
    modelPolicies: [],
    auto: true,
  },
  toolResultPruner: { thresholdChars: 8_192, headChars: 4_096, tailChars: 1_024 },
  agentLoop: { maxParallelToolCalls: 10 },
  persistence: { compression: 'zstd', listConcurrency: 3 },
  sessionQuery: {
    openAt: 'startup',
    journalMode: 'wal',
    defaultLimit: 20,
    maxLimit: 100,
    snippetChars: 240,
    readWindowMax: 50,
    persistedReadConcurrency: 4,
    preparedSessionCacheSize: 5,
  },
  projectionCache: { writeEveryEvents: 200, writeIntervalMs: 5_000 },
})

const dshSource = (packageName, field) => deepFreeze({
  kind: 'dsh_default', package: packageName, version: NATIVE_DSH_RELEASE, field,
})
const sourceSection = (packageName, value) => Object.fromEntries(
  Object.keys(value).map(field => [field, dshSource(packageName, field)]),
)
const compactionSources = sourceSection('@deepseek-ai/dsh-compaction-basic',
  NATIVE_DSH_DEFAULTS.compaction)
compactionSources.maxTokens = {
  kind: 'dsh_derived_default',
  package: '@deepseek-ai/dsh-compaction-basic',
  version: NATIVE_DSH_RELEASE,
  field: 'maxTokens',
  expression: 'headroomTokens',
}
export const NATIVE_DSH_DEFAULT_SOURCES = deepFreeze({
  compaction: compactionSources,
  toolResultPruner: sourceSection('@deepseek-ai/dsh-compaction-tool-result-pruner',
    NATIVE_DSH_DEFAULTS.toolResultPruner),
  agentLoop: sourceSection('@deepseek-ai/dsh-agent-loop', NATIVE_DSH_DEFAULTS.agentLoop),
  persistence: sourceSection('@deepseek-ai/dsh-session-persistence-jsonl',
    NATIVE_DSH_DEFAULTS.persistence),
  sessionQuery: sourceSection('@deepseek-ai/dsh-session-query-sqlite',
    NATIVE_DSH_DEFAULTS.sessionQuery),
  projectionCache: Object.fromEntries(Object.keys(NATIVE_DSH_DEFAULTS.projectionCache)
    .map(field => [field, deepFreeze({
      kind: 'app_deployment_required',
      package: '@deepseek-ai/dsh-session-projection-cache',
      version: NATIVE_DSH_RELEASE,
      field,
    })])),
})

function object(value, name) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${name} must be an object`)
  }
  return value
}

function known(value, fields, name) {
  for (const key of Object.keys(object(value, name))) {
    if (!fields.has(key)) throw new TypeError(`${name} contains unknown field ${key}`)
  }
}

function integer(value, name, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${name} must be an integer in [${minimum}, ${maximum}]`)
  }
  return value
}

function ratio(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0 || value > 1) {
    throw new TypeError(`${name} must be in (0, 1]`)
  }
  return value
}

function modelId(value, name, { empty = false } = {}) {
  if (empty && value === '') return value
  if (typeof value !== 'string' || !MODEL_ID.test(value)) {
    throw new TypeError(`${name} must be a bounded model identifier`)
  }
  return value
}

function reviewedModelId(value, name) {
  modelId(value, name)
  if (!REVIEWED_MODEL_IDS.has(value)) {
    throw new TypeError(`${name} is not an installed reviewed model`)
  }
  return value
}

function validateSummarizationPair(value, name) {
  const provider = value.summarizationProvider
  const model = value.summarizationModel
  if (provider === undefined && model === undefined) return
  if (typeof provider !== 'string' || typeof model !== 'string'
      || (provider === '') !== (model === '')) {
    throw new TypeError(`${name} summarization route must be an empty or non-empty pair`)
  }
  if (provider !== '') {
    if (provider !== 'deepseek-official') {
      throw new TypeError(`${name}.summarizationProvider is not reviewed`)
    }
    reviewedModelId(model, `${name}.summarizationModel`)
  }
}

function validateCompactionPolicy(value, name, { modelPolicy = false } = {}) {
  known(value, modelPolicy ? MODEL_POLICY_FIELDS : COMPACTION_FIELDS, name)
  if (modelPolicy) {
    if (value.provider !== 'deepseek-official') {
      throw new TypeError(`${name}.provider is not reviewed`)
    }
    reviewedModelId(value.model, `${name}.model`)
  }
  if (value.thresholdRatio !== undefined) ratio(value.thresholdRatio, `${name}.thresholdRatio`)
  if (value.headroomTokens !== undefined) integer(value.headroomTokens,
    `${name}.headroomTokens`)
  if (value.retainRatio !== undefined) ratio(value.retainRatio, `${name}.retainRatio`)
  if (value.retainTokens !== undefined) integer(value.retainTokens, `${name}.retainTokens`)
  if (value.retainRatio !== undefined && value.retainTokens !== undefined) {
    throw new TypeError(`${name} cannot set retainRatio and retainTokens together`)
  }
  if (value.maxTokens !== undefined) integer(value.maxTokens, `${name}.maxTokens`, 1)
  if (value.compactionRetries !== undefined) integer(value.compactionRetries,
    `${name}.compactionRetries`, 0, 16)
  if (value.maxOverflowRetries !== undefined) integer(value.maxOverflowRetries,
    `${name}.maxOverflowRetries`, 0, 8)
  validateSummarizationPair(value, name)
  if (!modelPolicy && value.auto !== undefined && typeof value.auto !== 'boolean') {
    throw new TypeError(`${name}.auto must be boolean`)
  }
  if (!modelPolicy && value.modelPolicies !== undefined) {
    if (!Array.isArray(value.modelPolicies) || value.modelPolicies.length > 32) {
      throw new TypeError(`${name}.modelPolicies must be a bounded array`)
    }
    const seen = new Set()
    value.modelPolicies.forEach((policy, index) => {
      validateCompactionPolicy(object(policy, `${name}.modelPolicies[${index}]`),
        `${name}.modelPolicies[${index}]`, { modelPolicy: true })
      const key = `${policy.provider}\u0000${policy.model}`
      if (seen.has(key)) throw new TypeError(`${name}.modelPolicies contains duplicate route`)
      seen.add(key)
    })
  }
}

function mergeCompaction(overrides) {
  validateCompactionPolicy(overrides, 'compaction')
  const result = { ...structuredClone(NATIVE_DSH_DEFAULTS.compaction),
    ...structuredClone(overrides) }
  if (overrides.retainTokens !== undefined) delete result.retainRatio
  if (overrides.retainRatio !== undefined) delete result.retainTokens
  if (overrides.maxTokens === undefined) {
    result.maxTokens = result.headroomTokens
  }
  const retention = result.retainRatio ?? result.retainTokens
  if (result.retainRatio !== undefined && result.retainRatio >= result.thresholdRatio) {
    throw new TypeError('compaction.retainRatio must be below thresholdRatio')
  }
  if (retention === undefined) throw new TypeError('compaction retention is missing')
  for (const [index, policy] of result.modelPolicies.entries()) {
    const threshold = policy.thresholdRatio ?? result.thresholdRatio
    const retain = policy.retainRatio ?? (policy.retainTokens === undefined
      ? result.retainRatio : undefined)
    if (retain !== undefined && retain >= threshold) {
      throw new TypeError(`compaction.modelPolicies[${index}] retention is invalid`)
    }
  }
  return result
}

function mergeSimple(defaults, overrides, fields, name) {
  known(overrides, fields, name)
  return { ...structuredClone(defaults), ...structuredClone(overrides) }
}

export function resolveNativeDshSettings(overrides = {}) {
  known(overrides, SECTIONS, 'DSH settings overrides')
  const pruner = mergeSimple(NATIVE_DSH_DEFAULTS.toolResultPruner,
    overrides.toolResultPruner ?? {}, new Set(['thresholdChars', 'headChars', 'tailChars']),
    'toolResultPruner')
  integer(pruner.thresholdChars, 'toolResultPruner.thresholdChars', 1)
  integer(pruner.headChars, 'toolResultPruner.headChars')
  integer(pruner.tailChars, 'toolResultPruner.tailChars')
  const pruneMarkerChars = Array.from(PRUNE_MARKER).length
  if (pruner.headChars + pruneMarkerChars + pruner.tailChars > pruner.thresholdChars) {
    throw new TypeError('toolResultPruner retained text exceeds thresholdChars')
  }

  const agentLoop = mergeSimple(NATIVE_DSH_DEFAULTS.agentLoop,
    overrides.agentLoop ?? {}, new Set(['maxParallelToolCalls']), 'agentLoop')
  integer(agentLoop.maxParallelToolCalls, 'agentLoop.maxParallelToolCalls', 1, 128)

  const persistence = mergeSimple(NATIVE_DSH_DEFAULTS.persistence,
    overrides.persistence ?? {}, new Set(['listConcurrency']), 'persistence')
  integer(persistence.listConcurrency, 'persistence.listConcurrency', 1, 128)

  const sessionQuery = mergeSimple(NATIVE_DSH_DEFAULTS.sessionQuery,
    overrides.sessionQuery ?? {}, new Set(Object.keys(NATIVE_DSH_DEFAULTS.sessionQuery)),
    'sessionQuery')
  if (!['startup', 'first-search', 'never'].includes(sessionQuery.openAt)
      || !['wal', 'delete', 'truncate', 'persist'].includes(sessionQuery.journalMode)) {
    throw new TypeError('sessionQuery mode is unsupported')
  }
  for (const field of ['defaultLimit', 'maxLimit', 'snippetChars',
    'persistedReadConcurrency', 'preparedSessionCacheSize']) {
    integer(sessionQuery[field], `sessionQuery.${field}`, 1, 1_000_000)
  }
  integer(sessionQuery.readWindowMax, 'sessionQuery.readWindowMax', 0, 1_000_000)
  if (sessionQuery.defaultLimit > sessionQuery.maxLimit) {
    throw new TypeError('sessionQuery.defaultLimit exceeds maxLimit')
  }

  const projectionCache = mergeSimple(NATIVE_DSH_DEFAULTS.projectionCache,
    overrides.projectionCache ?? {}, new Set(['writeEveryEvents', 'writeIntervalMs']),
    'projectionCache')
  integer(projectionCache.writeEveryEvents, 'projectionCache.writeEveryEvents', 1,
    1_000_000)
  integer(projectionCache.writeIntervalMs, 'projectionCache.writeIntervalMs', 1,
    86_400_000)

  return deepFreeze({
    compaction: mergeCompaction(overrides.compaction ?? {}),
    toolResultPruner: pruner,
    agentLoop,
    persistence,
    sessionQuery,
    projectionCache,
  })
}

function fieldSources(overrides, source) {
  const result = structuredClone(NATIVE_DSH_DEFAULT_SOURCES)
  for (const [section, values] of Object.entries(overrides)) {
    result[section] ??= {}
    for (const field of Object.keys(values)) {
      result[section][field] = { kind: 'user_override', source, field }
    }
  }
  return result
}

export function defaultNativeDshSettingsSnapshot() {
  return deepFreeze({
    contract: NATIVE_DSH_SETTINGS_CONTRACT,
    policy_version: NATIVE_DSH_SETTINGS_VERSION,
    generation: 0,
    source: 'builtin',
    updated_at: null,
    overrides: {},
    effective: resolveNativeDshSettings(),
    defaults: structuredClone(NATIVE_DSH_DEFAULTS),
    default_sources: structuredClone(NATIVE_DSH_DEFAULT_SOURCES),
    field_sources: fieldSources({}, 'builtin'),
    apply_scope: 'next_runtime',
  })
}

export function validateNativeDshSettingsStore(value) {
  known(value, new Set([
    'contract', 'base_policy_version', 'generation', 'overrides', 'source', 'updated_at',
  ]), 'DSH settings store')
  if (value.contract !== NATIVE_DSH_SETTINGS_CONTRACT
      || value.base_policy_version !== NATIVE_DSH_SETTINGS_VERSION
      || !Number.isSafeInteger(value.generation) || value.generation < 1
      || typeof value.source !== 'string' || !SOURCE.test(value.source)
      || typeof value.updated_at !== 'string' || Number.isNaN(Date.parse(value.updated_at))) {
    throw new TypeError('DSH settings store identity is invalid')
  }
  const effective = resolveNativeDshSettings(value.overrides)
  return deepFreeze({
    contract: NATIVE_DSH_SETTINGS_CONTRACT,
    policy_version: NATIVE_DSH_SETTINGS_VERSION,
    generation: value.generation,
    source: value.source,
    updated_at: value.updated_at,
    overrides: structuredClone(value.overrides),
    effective,
    defaults: structuredClone(NATIVE_DSH_DEFAULTS),
    default_sources: structuredClone(NATIVE_DSH_DEFAULT_SOURCES),
    field_sources: fieldSources(value.overrides, value.source),
    apply_scope: 'next_runtime',
  })
}

export async function loadNativeDshSettings(profileRoot) {
  const path = resolve(profileRoot, NATIVE_DSH_SETTINGS_FILE)
  let raw
  try {
    raw = await readFile(path, 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT') return defaultNativeDshSettingsSnapshot()
    throw error
  }
  if (raw.length > 256 * 1_024) throw new TypeError('DSH settings file is too large')
  return validateNativeDshSettingsStore(JSON.parse(raw))
}
