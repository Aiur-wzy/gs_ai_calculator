/** Official base profile + one App bundle; no second generic Agent owner. */
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import { withFileLock, writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import {
  createRuntimeResolution, loadProfileDirectory, mountRootInclude, PluginPackages,
  readProfilePatches, recomposeProfile,
} from '@deepseek-ai/dsh-app-boot'

const installAnchor = fileURLToPath(new URL('../package.json', import.meta.url))
const BASE = '@deepseek-ai/dsh-base'
const BUNDLE = '@genshin-calc/native-app-profile'
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)

/** Explicit product policies, not a generic-tool denylist. */
export const APP_PROFILE_POLICY = Object.freeze({
  provider: 'dsh-native-provider-directory', credentials: 'windows-protected-profile',
  sessionUpload: false, telemetry: false, publicListener: false,
})

const disable = id => ({ id, disabled: true })
const override = (id, config) => ({ id, config })

export async function preserveMigrationBackup(path, content) {
  // A completed backup can survive a crash before patch/marker publication.
  // Reusing those exact bytes is safe; overwriting different history is not.
  return withFileLock(path, async () => {
    const prior = await readFile(path, 'utf8').catch(error => error.code === 'ENOENT' ? null : Promise.reject(error))
    if (prior !== null) {
      if (prior !== content) throw new Error('native_dsh_settings_migration_backup_conflict')
      return
    }
    await writeFileAtomic(path, content, { mode: 0o600, dirMode: 0o700 })
  })
}

export async function createNativeFullProfile(ctx, {
  paths, personaPrefix, dshSettings = {}, providerRetryPolicy, model = 'deepseek-flash',
  reasoningEffort = 'high',
}) {
  const dir = join(paths.profileRoot, 'runtime-profile')
  const home = paths.profileRoot
  const bundleDir = join(dir, 'node_modules', '@genshin-calc', 'native-app-profile')
  const workspace = join(paths.profileRoot, 'workspace')
  await mkdir(bundleDir, { recursive: true })
  await mkdir(workspace, { recursive: true })
  const defaultsPath = join(bundleDir, 'defaults.json')
  const basePath = join(dir, 'base.json')
  const legacyMap = {
    persistence: 'session-persistence-jsonl', agentLoop: 'agent-loop',
    projectionCache: 'session-projection-cache', toolResultPruner: 'tool-result-pruner',
    compaction: 'compaction-basic', sessionQuery: 'session-query-sqlite',
  }
  // A one-time compatibility input. Subsequent edits belong to DSH's profile
  // ConfigEditor; do not keep applying a Python/JS mirror above user settings.
  const migrationPath = join(dir, 'legacy-settings-migration.json')
  let migrated = false
  try { migrated = object(JSON.parse(await readFile(migrationPath, 'utf8'))) }
  catch (error) { if (error.code !== 'ENOENT') throw error }
  const legacyRows = migrated ? [] : Object.entries(dshSettings).map(([name, config]) => {
    if (!Object.hasOwn(legacyMap, name) || !object(config)) throw new Error('native_dsh_legacy_settings_invalid')
    return override(legacyMap[name], structuredClone(config))
  })
  const appRows = [
    { insert: [{ id: 'genshin-settings-controller', name: new URL('./native-dsh-app-settings.mjs', import.meta.url).href }] },
    override('session-persistence-jsonl', { root: paths.sessionRoot }),
    override('storage-json', { root: paths.storageRoot }),
    // Scope attachments even when the Host is embedded without a launcher
    // environment. The official store must not fall back to the user's DSH.
    override('attachment-local', { dshHome: join(paths.profileRoot, 'dsh-home') }),
    override('session-query-sqlite', { path: paths.queryPath, openAt: 'startup' }),
    override('system-prompt', { personaPrefix, includeHarnessIdentity: false, includeRuntimeContext: false }),
    override('sandbox-policy', { workspaceRoot: workspace, mode: 'workspace-write' }),
    override('agent-default-model', { provider: 'deepseek-official', model, reasoningEffort }),
    // The App injects the sole credential provider before mounting this tree.
    disable('credentials'), disable('session-log-deepseek'),
    disable('session-telemetry-otel'), disable('otel'),
    // Product search/fetch uses upstream implementations with the existing
    // public-only fetch + explicit research-accounting policy.
    disable('timeout-policy'),
    // A title is not permission to initiate an unbudgeted auxiliary request.
    // The ordinary DSH title projection remains available.
    disable('session-title-llm'),
    override('llm-deepseek', {
      baseURL: 'https://api.deepseek.com/anthropic', reasoningEffort: 'high',
      streamIdleTimeoutMs: 300_000, retryPolicy: structuredClone(providerRetryPolicy),
    }),
  ]
  const lockedRows = [
    disable('credentials'), disable('session-log-deepseek'),
    disable('session-telemetry-otel'), disable('otel'),
  ]
  let clientRows = []
  const writeBundle = async () => writeFile(defaultsPath, JSON.stringify([
    ...appRows, ...(clientRows.length ? [{ insert: clientRows }] : []),
  ], null, 2) + '\n')
  await writeFile(join(bundleDir, 'package.json'), JSON.stringify({ name: BUNDLE, version: '0.46.0',
    private: true, dsh: { bundle: { patch: './defaults.json' } } }) + '\n')
  await writeFile(join(dir, 'package.json'), JSON.stringify({ private: true,
    dsh: { profile: { bundles: [BASE, BUNDLE] } } }) + '\n')
  await writeFile(basePath, '[]\n')
  await writeBundle()
  const profile = Object.freeze({ name: 'genshin', dir, patchPath: join(dir, 'cordis.patch.yml'),
    installAnchor, cwd: workspace, home, startedBundles: [BASE, BUNDLE],
    overlays: lockedRows, telemetryDisabledEnv: '1' })
  const loaded = loadProfileDirectory('genshin', dir, installAnchor)
  if (loaded.skippedBundles.length) throw new Error('native_dsh_full_profile_bundle_missing')
  ctx.provide('profileContext', profile)
  ctx.provide('dshHomePath', (...parts) => resolve(home, ...parts))
  ctx.baseUrl = pathToFileURL(dir + '/').href
  // Public readiness contract consumed by upstream profile HMR. This marks
  // only App boot completion; it owns no Agent/Turn/Session state.
  let ready = false
  const readyListeners = new Set()
  ctx.provide('appReady', Object.freeze({ onReady(listener) {
    if (ready) listener()
    else readyListeners.add(listener)
    return () => readyListeners.delete(listener)
  } }))
  await ctx.plugin(Loader, { baseUrl: import.meta.url })
  await ctx.plugin(PluginPackages, { resolution: await createRuntimeResolution({ installAnchor, home, profile: loaded }) })
  const patches = readProfilePatches('genshin', profile, loaded)
  if (legacyRows.length) {
    // Only seed absent namespaces; an explicit upstream user patch wins.
    const existing = loaded.patches ?? []
    const seed = legacyRows.filter(row => !existing.some(other => other.id === row.id)).map(row => ({
      ...row, config: { ...(appRows.find(base => base.id === row.id)?.config ?? {}), ...row.config },
    }))
    if (seed.length) {
      const target = profile.patchPath
      const prior = await readFile(target, 'utf8').catch(error => error.code === 'ENOENT' ? null : Promise.reject(error))
      if (prior !== null) await preserveMigrationBackup(join(dir, 'pre-migration-cordis.patch.yml'), prior)
      // Do not parse a user's YAML ourselves: the public profile loader has
      // already parsed it losslessly, including supported expressions.
      await writeFileAtomic(target, JSON.stringify([...seed, ...existing], null, 2) + '\n', { mode: 0o600, dirMode: 0o700 })
    }
  }
  if (!migrated) await writeFileAtomic(migrationPath, JSON.stringify({ contract: 'genshin-dsh-settings-migration-v1',
    imported_sections: Object.keys(dshSettings), owner: 'dsh-config-editor' }) + '\n', { mode: 0o600, dirMode: 0o700 })
  await mountRootInclude(ctx, basePath, legacyRows.length
    ? readProfilePatches('genshin', profile) : patches, import.meta.url, 'genshin')
  await ctx.loader.await()
  const report = () => [...ctx.loader.entries()].filter(entry => typeof entry.options.id === 'string')
    .map(entry => ({ id: entry.options.id, name: entry.options.name, disabled: entry.options.disabled === true,
      state: entry.fiber?.state ?? null }))
  const assertHealthy = () => {
    const failed = report().filter(row => row.state === 3 && !row.disabled)
    if (failed.length) throw Object.assign(new Error('native_dsh_full_profile_failed'), { details: failed })
  }
  assertHealthy()
  ready = true
  for (const listener of readyListeners) listener()
  readyListeners.clear()
  const baseProfileSha256 = createHash('sha256').update(await readFile(
    fileURLToPath(import.meta.resolve('@deepseek-ai/dsh-base/cordis.patch.yml')))).digest('hex')
  return Object.freeze({ contract: 'genshin-native-dsh-full-profile-v1', profile, workspace,
    baseProfileSha256, appPolicy: APP_PROFILE_POLICY,
    rows: report, assertHealthy,
    async extendClientRows(rows) {
      if (!Array.isArray(rows)) throw new TypeError('native_dsh_client_rows_required')
      // Reuse rows which base already owns; never insert another service owner.
      const names = new Set(report().filter(row => !row.disabled).map(row => row.name))
      clientRows = rows.filter(row => !names.has(row.name))
      await writeBundle()
      await recomposeProfile(ctx, readProfilePatches('genshin', profile), 'genshin')
      await ctx.loader.await()
      assertHealthy()
      return { namespaces: () => ctx.settings.describe({ redactSecrets: true }).map(row => row.ns) }
    },
  })
}
