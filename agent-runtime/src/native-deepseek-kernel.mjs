/** Official DSH kernel; SessionController owns all Session and Turn operations. */
import { Context } from '@deepseek-ai/cordis'
import {
  DSH_LAUNCH_ENVIRONMENT_KEY,
  createLaunchEnvironmentSnapshot,
} from '@deepseek-ai/dsh-launch-environment'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

import { NativeMemoryCredentials } from './memory-credentials.mjs'
import { installNativeProviderProxy } from './native-provider-proxy.mjs'
import { mountNativeWebRuntime } from './native-dsh-web-stack.mjs'
import { NativeSecretController } from './secret-control.mjs'
import { createNativeFullProfile } from './native-dsh-full-profile.mjs'
import {
  PRODUCTION_COMPOSITION_CONTRACT,
  PRODUCTION_COMPACTION_POLICY_ID,
  PRODUCTION_COMPACTION_POLICY_VERSION,
  PRODUCTION_DENIED_PACKAGE_TOKENS,
  PRODUCTION_REQUIRED_PROJECTIONS,
  PRODUCTION_REQUIRED_SERVICES,
  productionCompositionIdentityHash,
  resolveProductionCompositionPaths,
} from './production-composition-contract.mjs'

export const DEEPSEEK_PROVIDER_ID = 'deepseek-official'
export const DEEPSEEK_OFFICIAL_MESSAGES_ROOT = 'https://api.deepseek.com/anthropic'
// Follow the pinned DSH adapter default.  This measures consecutive silence
// on one remote SSE response; it is not the App turn budget and it is not the
// local sidecar heartbeat window.  A model that continues streaming remains
// eligible for the much longer App-owned active-work budget.
export const DEEPSEEK_STREAM_IDLE_TIMEOUT_MS = 300_000

export async function createNativeDeepSeekKernel({
  personaPrefix = 'Genshin native Agent.',
  dshSettings,
  profileRoot,
  sessionRoot,
  storageRoot,
  queryPath,
  // Trusted composition seam only. Shell hosts may add process-local DSH
  // Connection records while inheriting the same Windows-injected API key.
  // Never sourced from model, GUI, persisted settings or launch JSON.
  credentialsPlugin = NativeMemoryCredentials,
  providerRetryPolicy = { mode: 'normal' },
  model = 'deepseek-flash',
  reasoningEffort = 'high',
} = {}) {
  if (providerRetryPolicy?.mode !== 'normal') throw new TypeError('native_provider_retry_requires_normal')
  if (typeof personaPrefix !== 'string' || personaPrefix.length < 1
      || personaPrefix.length > 16_384) {
    throw new TypeError('personaPrefix must be a bounded non-empty string')
  }
  const compositionPaths = resolveProductionCompositionPaths({
    profileRoot,
    sessionRoot,
    storageRoot,
    queryPath,
  })
  await mkdir(compositionPaths.profileRoot, { recursive: true })
  const ctx = new Context()
  let disposed = false
  let secretController
  let webRuntime
  let providerProxy
  let fullProfile
  try {
    providerProxy = await installNativeProviderProxy()
    ctx.provide(DSH_LAUNCH_ENVIRONMENT_KEY, createLaunchEnvironmentSnapshot([]))
    await ctx.plugin(credentialsPlugin, { directory: join(compositionPaths.profileRoot, 'credentials') })
    fullProfile = await createNativeFullProfile(ctx, {
      paths: compositionPaths, personaPrefix, dshSettings, providerRetryPolicy, model, reasoningEffort,
    })
    ctx.provide('genshinFullProfile', fullProfile)
    webRuntime = await mountNativeWebRuntime(ctx)
    secretController = new NativeSecretController(ctx.credentials)
  } catch (error) {
    await ctx.fiber.dispose().catch(() => {})
    await providerProxy?.dispose().catch(() => {})
    throw error
  }

  return Object.freeze({
    context: ctx,
    compositionContract: PRODUCTION_COMPOSITION_CONTRACT,
    get compositionRows() { return Object.freeze(fullProfile.rows()) },
    fullProfile,
    compactionPolicy: Object.freeze({
      id: PRODUCTION_COMPACTION_POLICY_ID,
      version: PRODUCTION_COMPACTION_POLICY_VERSION,
      owner: '@deepseek-ai/dsh-compaction-basic',
      settings: 'official-profile-config-editor',
    }),
    compositionPaths,
    secretController,
    webRuntime,

    async flushSessions() {
      if (disposed) throw new Error('native_deepseek_kernel_disposed')
      await ctx.sessionPersistence.flush()
    },

    assertProductionServices() {
      if (disposed) throw new Error('native_deepseek_kernel_disposed')
      const missing = PRODUCTION_REQUIRED_SERVICES.filter(name => ctx.get(name) === undefined)
      if (missing.length > 0) {
        throw new Error(`native_dsh_production_services_missing:${missing.join(',')}`)
      }
      return Object.freeze([...PRODUCTION_REQUIRED_SERVICES])
    },

    productionCompositionReport() {
      if (disposed) throw new Error('native_deepseek_kernel_disposed')
      fullProfile.assertHealthy()
      const rows = fullProfile.rows()
      const mountedNames = rows.map(row => row.name)
      const denied = PRODUCTION_DENIED_PACKAGE_TOKENS.filter(token => (
        mountedNames.some(name => name.includes(token))
      ))
      if (denied.length > 0) {
        throw new Error(`native_dsh_denied_package_mounted:${denied.join(',')}`)
      }
      return Object.freeze({
        contract: PRODUCTION_COMPOSITION_CONTRACT,
        composition_hash: productionCompositionIdentityHash(fullProfile),
        rows: Object.freeze(rows),
        denied_packages_present: Object.freeze([]),
      })
    },

    async dispose() {
      if (disposed) return
      disposed = true
      try {
        await secretController?.dispose().catch(() => {})
        await ctx.fiber.dispose()
      } finally {
        await providerProxy?.dispose().catch(() => {})
      }
    },
  })
}
