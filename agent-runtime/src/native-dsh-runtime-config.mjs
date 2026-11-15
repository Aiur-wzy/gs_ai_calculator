/** Host-only launch configuration; credentials never enter argv or settings. */
import { isAbsolute, resolve, join, sep } from 'node:path'
import { pathToFileURL } from 'node:url'
import { readFile } from 'node:fs/promises'
import { loadNativeDshSettings, resolveNativeDshSettings, NATIVE_DSH_REVIEWED_MODEL_IDS } from './native-dsh-settings.mjs'

export const NATIVE_DSH_RUNTIME_CONTRACT = 'genshin-native-dsh-runtime-v1'
export const NATIVE_DSH_RUNTIME_CONTROL_CONTRACT = 'genshin-native-dsh-runtime-control-v1'
const OPTIONS = new Set(['model', 'reasoningEffort', 'dshSettings', 'providerRetryPolicy', 'providerCapacity'])
const EFFORTS = new Set(['off', 'low', 'high', 'max'])
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const absolute = (value, name) => {
  if (typeof value !== 'string' || !isAbsolute(value) || value.includes('\0')) {
    throw new TypeError(`native_dsh_runtime_${name}_invalid`)
  }
  return resolve(value)
}

export async function readNativeDshRuntimeConfig(args) {
  if (!Array.isArray(args) || args.length < 3 || args.length > 4) {
    throw new TypeError('native_dsh_runtime_arguments_invalid')
  }
  const [profile, mirror, assets, raw = '{}'] = args
  const profileRoot = absolute(profile, 'profile_root')
  const mirrorRoot = absolute(mirror, 'mirror_root')
  const assetsRoot = absolute(assets, 'assets_root')
  let options
  try { options = JSON.parse(raw) } catch { throw new TypeError('native_dsh_runtime_options_json_invalid') }
  if (!object(options) || Object.keys(options).some(key => !OPTIONS.has(key))) {
    throw new TypeError('native_dsh_runtime_options_invalid')
  }
  if (options.model !== undefined && !NATIVE_DSH_REVIEWED_MODEL_IDS.includes(options.model)) {
    throw new TypeError('native_dsh_runtime_model_not_reviewed')
  }
  if (options.reasoningEffort !== undefined && !EFFORTS.has(options.reasoningEffort)) {
    throw new TypeError('native_dsh_runtime_reasoning_effort_invalid')
  }
  if (options.providerCapacity !== undefined
      && (!Number.isSafeInteger(options.providerCapacity) || options.providerCapacity < 1)) {
    throw new TypeError('native_dsh_runtime_provider_capacity_invalid')
  }
  const migrated = await readFile(join(profileRoot, 'runtime-profile', 'legacy-settings-migration.json'), 'utf8')
    .then(() => true, error => error.code === 'ENOENT' ? false : Promise.reject(error))
  const settings = migrated ? {} : options.dshSettings === undefined
    ? (await loadNativeDshSettings(profileRoot)).overrides : options.dshSettings
  // Preserve sparse overrides. DSH itself resolves its defaults at mount.
  resolveNativeDshSettings(settings)
  if (options.providerRetryPolicy !== undefined
      && (!object(options.providerRetryPolicy) || options.providerRetryPolicy.mode !== 'normal')) {
    throw new TypeError('native_dsh_runtime_retry_requires_normal')
  }
  const clientAssets = {
    contract: JSON.parse(await readFile(join(assetsRoot, 'dsh-client-composition.json'), 'utf8')),
    acceptedManifest: JSON.parse(await readFile(join(assetsRoot, 'dsh-client-accepted-manifest.json'), 'utf8')),
    localModuleBaseUrl: pathToFileURL(`${assetsRoot}${sep}`).href,
  }
  return Object.freeze({ profileRoot, mirrorRoot, clientAssets,
    dshSettings: structuredClone(settings), model: options.model ?? 'deepseek-flash',
    reasoningEffort: options.reasoningEffort ?? 'high',
    providerRetryPolicy: structuredClone(options.providerRetryPolicy ?? { mode: 'normal' }),
    providerCapacity: options.providerCapacity ?? 2,
  })
}
