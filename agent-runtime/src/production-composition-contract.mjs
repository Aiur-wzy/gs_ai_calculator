/** One auditable DSH production-composition contract shared by profile and kernel. */
import { createHash } from 'node:crypto'
import { dirname, isAbsolute, relative, resolve } from 'node:path'

export const PRODUCTION_COMPOSITION_CONTRACT = 'genshin-native-dsh-production-v1'
export const NATIVE_DSH_PROFILE_ROOT_ENV = 'GENSHIN_NATIVE_DSH_PROFILE_ROOT'
// Compression policy and all generic defaults belong to the installed DSH
// plugin/profile, not an App mirror of its configuration fields.
export const PRODUCTION_COMPACTION_POLICY_ID = 'dsh-compaction-basic'
export const PRODUCTION_COMPACTION_POLICY_VERSION = 1

export const PRODUCTION_REQUIRED_SERVICES = Object.freeze([
  'llm',
  'sessions',
  'sessionPersistence',
  'sessionProjections',
  'userQuestions',
  'systemPrompt',
  'tools',
  'agents',
  'agentLoop',
  'storage',
  'storageDomain',
  'sessionProjectionCache',
  'tokenMeter',
  'toolResultPruner',
  'compaction',
  'sessionQuery',
])

export const PRODUCTION_REQUIRED_PROJECTIONS = Object.freeze([
  'contextBreakdown',
  'contextPressure',
  'sessionStats',
  'tokenUsage',
  'turnOutline',
  'userQuestions',
])

/** Packages which may be installed transitively but must never be mounted. */
// NA-20/22: generic capabilities follow the official base composition.
// Explicit credential/provider/upload policies are profile overrides, not a
// broad prohibition on filesystem, shell, sandbox or collaboration packages.
export const PRODUCTION_DENIED_PACKAGE_TOKENS = Object.freeze([])

function absolutePath(value, name) {
  if (typeof value !== 'string' || value.length < 1 || value.includes('\u0000')
      || !isAbsolute(value)) {
    throw new TypeError(`${name} must be an absolute non-empty path`)
  }
  return resolve(value)
}

function inside(parent, child, name) {
  const suffix = relative(parent, child)
  if (suffix === '' || suffix.startsWith('..') || isAbsolute(suffix)) {
    throw new TypeError(`${name} must be a descendant of profileRoot`)
  }
  return child
}

/**
 * Validate the four production-owned paths.  `sessionPersistenceRoot` is a
 * compatibility alias for old probes/callers; it deterministically derives
 * the other three paths but is never consulted once explicit paths exist.
 */
export function resolveProductionCompositionPaths({
  profileRoot,
  sessionRoot,
  storageRoot,
  queryPath,
  sessionPersistenceRoot,
} = {}) {
  if (profileRoot === undefined && sessionRoot === undefined
      && storageRoot === undefined && queryPath === undefined
      && sessionPersistenceRoot !== undefined) {
    const legacySessionRoot = absolutePath(sessionPersistenceRoot, 'sessionPersistenceRoot')
    const legacyProfileRoot = dirname(legacySessionRoot)
    return Object.freeze({
      profileRoot: legacyProfileRoot,
      sessionRoot: legacySessionRoot,
      storageRoot: resolve(legacyProfileRoot, 'storages'),
      queryPath: resolve(legacyProfileRoot, 'session-query.sqlite'),
      compatibilityDerived: true,
    })
  }
  const profile = absolutePath(profileRoot, 'profileRoot')
  const sessions = inside(profile, absolutePath(sessionRoot, 'sessionRoot'), 'sessionRoot')
  const storages = inside(profile, absolutePath(storageRoot, 'storageRoot'), 'storageRoot')
  const query = inside(profile, absolutePath(queryPath, 'queryPath'), 'queryPath')
  return Object.freeze({
    profileRoot: profile,
    sessionRoot: sessions,
    storageRoot: storages,
    queryPath: query,
    compatibilityDerived: false,
  })
}

/**
 * Derive the fixed production layout from the single App-owned durable root.
 * Sidecars accept no independently chosen child paths, so an injected session
 * or SQLite path cannot escape the reviewed profile boundary.
 */
export function productionCompositionPathsFromProfileRoot(profileRoot) {
  const profile = absolutePath(profileRoot, 'profileRoot')
  return resolveProductionCompositionPaths({
    profileRoot: profile,
    sessionRoot: resolve(profile, 'sessions'),
    storageRoot: resolve(profile, 'storages'),
    queryPath: resolve(profile, 'session-query.sqlite'),
  })
}

/** Stable policy identity from the real public base, never a hand-picked list. */
export function productionCompositionIdentityHash({ baseProfileSha256, appPolicy }) {
  if (!/^[0-9a-f]{64}$/u.test(baseProfileSha256)) throw new TypeError('base profile SHA required')
  return createHash('sha256').update(JSON.stringify({
    contract: PRODUCTION_COMPOSITION_CONTRACT,
    baseProfileSha256,
    appPolicy,
    requiredServices: PRODUCTION_REQUIRED_SERVICES,
    requiredProjections: PRODUCTION_REQUIRED_PROJECTIONS,
  }), 'utf8').digest('hex')
}
