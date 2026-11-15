/** Fixed reviewed profile using DSH's production Settings/ConfigEditor owner. */
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRuntimeResolution, loadProfileDirectory, mountRootInclude, PluginPackages, readProfilePatches } from '@deepseek-ai/dsh-app-boot'
import ConfigEditor from '@deepseek-ai/dsh-config-editor'
import Settings from '@deepseek-ai/dsh-settings'
import SettingsController from '@deepseek-ai/dsh-api-settings-controller'
import * as Locale from '@deepseek-ai/dsh-client-locale'
import * as Theme from '@deepseek-ai/dsh-client-ui-theme'
import * as Shortcuts from '@deepseek-ai/dsh-client-shortcuts'
import * as Conversation from '@deepseek-ai/dsh-client-ui-conversation'
import * as Chat from '@deepseek-ai/dsh-client-ui-chat'
import * as UiSettings from '@deepseek-ai/dsh-client-ui-settings'
import * as General from '@deepseek-ai/dsh-client-ui-settings-general'

const own = (value, key) => Object.hasOwn(value, key)
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const fail = code => { throw Object.assign(new Error(code), { code }) }
export const NATIVE_DSH_PROFILE_MODULES = Object.freeze([
  '@deepseek-ai/cordis-plugin-include', '@deepseek-ai/dsh-app-boot',
  '@deepseek-ai/dsh-config-editor', '@deepseek-ai/dsh-settings',
  '@deepseek-ai/dsh-api-settings-controller',
])
const EDITABLE_CONFIGS = new Map([
  ['@deepseek-ai/dsh-client-locale', Locale.Config],
  ['@deepseek-ai/dsh-client-ui-theme', Theme.Config],
  ['@deepseek-ai/dsh-client-shortcuts', Shortcuts.Config],
  ['@deepseek-ai/dsh-client-ui-conversation', Conversation.Config],
  ['@deepseek-ai/dsh-client-ui-chat', Chat.Config],
  ['@deepseek-ai/dsh-client-ui-settings', UiSettings.Config],
  ['@deepseek-ai/dsh-client-ui-settings-general', General.Config],
])
class NativeSettingsController extends SettingsController {
  describe() { return { ...super.describe(), hasDocument: false } }
}

function rejectExpressions(value) {
  if (Array.isArray(value)) return value.forEach(rejectExpressions)
  if (!object(value)) return
  if (own(value, '__jsExpr')) fail('native_dsh_settings_expression_forbidden')
  Object.values(value).forEach(rejectExpressions)
}

function editablePatch(schema, value) {
  rejectExpressions(value)
  const definitions = schema.refs
  const visit = (uid, selected) => {
    const node = definitions[uid]
    if (node?.meta?.volatile === true) return
    if (!object(selected) || node?.type !== 'object') fail('native_dsh_settings_nonvolatile_patch')
    for (const [key, child] of Object.entries(selected)) {
      if (!own(node.dict ?? {}, key)) fail('native_dsh_settings_nonvolatile_patch')
      visit(node.dict[key], child)
    }
  }
  visit(schema.uid, value)
}

/** Stable SDK preference namespaces, independent from package identifiers. */
export const nativeDshSettingsEntryId = name => name.startsWith('@deepseek-ai/dsh-client-')
  ? name.slice('@deepseek-ai/dsh-client-'.length)
  : name.startsWith('@deepseek-ai/dsh-') ? name.slice('@deepseek-ai/dsh-'.length)
    : 'genshin-client-domain'

export async function mountNativeDshProfileSettings({ context: ctx, profileRoot, entries }) {
  if (ctx.get('profileContext') !== undefined || typeof profileRoot !== 'string') {
    fail('native_dsh_settings_profile_conflict')
  }
  const dir = join(profileRoot, 'client-profile')
  const home = join(dir, 'isolated-home')
  const bundle = join(dir, 'node_modules', '@genshin-calc', 'dsh-client-profile')
  await mkdir(home, { recursive: true })
  await mkdir(bundle, { recursive: true })
  const rows = entries.map(row => ({ ...row, id: nativeDshSettingsEntryId(row.packageName),
    config: { ...(row.packageName === '@deepseek-ai/dsh-client-locale' ? { preference: 'zh' } : {}),
      ...(row.config ?? {}) } }))
  const byId = new Map()
  for (const row of rows) {
    if (byId.has(row.id)) fail('native_dsh_settings_entry_id_conflict')
    const config = EDITABLE_CONFIGS.get(row.packageName)
    byId.set(row.id, { row, schema: config?.toJSON?.() })
    delete row.packageName
  }
  // This tiny generated bundle contains data only. Actual code still resolves
  // from the pinned runtime tree or the single reviewed App host entry.
  await writeFile(join(bundle, 'package.json'), JSON.stringify({ name: '@genshin-calc/dsh-client-profile',
    version: '0.46.0', private: true, dsh: { bundle: { patch: './defaults.json' } } }))
  await writeFile(join(bundle, 'defaults.json'), JSON.stringify([{ insert: rows }]))
  await writeFile(join(dir, 'package.json'), JSON.stringify({ private: true,
    dsh: { profile: { bundles: ['@genshin-calc/dsh-client-profile'] } } }))
  const base = join(dir, 'base.json')
  await writeFile(base, '[]\n')
  const profile = Object.freeze({ name: 'genshin-client', dir, patchPath: join(dir, 'cordis.patch.yml'),
    installAnchor: fileURLToPath(new URL('../package.json', import.meta.url)), cwd: dir, home,
    startedBundles: ['@genshin-calc/dsh-client-profile'], overlays: [], telemetryDisabledEnv: '1' })
  const loaded = loadProfileDirectory('genshin', dir, profile.installAnchor)
  if (loaded.skippedBundles.length !== 0) fail('native_dsh_settings_profile_bundle_missing')
  for (const patch of loaded.patches) {
    if (!object(patch) || Object.keys(patch).some(key => !['id', 'name', 'config'].includes(key))
        || !own(patch, 'config')) fail('native_dsh_settings_profile_patch_forbidden')
    const target = byId.get(patch.id)
    if (target?.schema === undefined || patch.name !== undefined && patch.name !== target.row.name) {
      fail('native_dsh_settings_profile_entry_forbidden')
    }
    editablePatch(target.schema, patch.config)
  }
  ctx.provide('profileContext', profile)
  await ctx.plugin(PluginPackages, { resolution: await createRuntimeResolution({
    installAnchor: profile.installAnchor, home, profile: loaded,
  }) })
  await ctx.plugin(ConfigEditor)
  await ctx.plugin(Settings)
  // Official SettingsController also supplies a Credentials namespace. Its
  // isolated empty credential scope makes that namespace unable to read or
  // mutate the App's in-memory provider key or Connection credentials.
  await ctx.isolate('credentials').plugin(NativeSettingsController, {
    openTextFile: async () => fail('native_dsh_settings_use_app_file_interface'),
  })
  await mountRootInclude(ctx, base, readProfilePatches('genshin', profile, loaded), import.meta.url, 'genshin')
  await ctx.loader.await()
  if (ctx.settings.describe().find(row => row.ns === 'locale')?.value?.preference !== 'zh'
      && !loaded.patches.some(row => row.id === 'locale')) fail('native_dsh_settings_default_locale_missing')
  return Object.freeze({ contract: 'genshin-native-dsh-profile-settings-v1',
    namespaces: () => ctx.settings.describe({ redactSecrets: true }).map(row => row.ns) })
}
