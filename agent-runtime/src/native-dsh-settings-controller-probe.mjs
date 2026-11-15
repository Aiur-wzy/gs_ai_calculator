/** Actual SDK configuration owner, Remote edits, and cold preference replay. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/sdk-settings-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
let network = 0
globalThis.fetch = () => { network += 1; throw new Error('No provider needed for user preferences') }
const clientAssets = {
  contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
  acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
  localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href,
}
let host, peer
const boot = async () => {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'), clientAssets })
  peer = host.createPeer()
}
const rpc = async (endpoint, args = {}) => {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: endpoint, payload: { args } }),
  }), peer)
  return (await response.json()).result
}
const evidence = {}
try {
  await boot()
  const initial = await rpc('settings/describe')
  assert.equal(initial.ok, true, JSON.stringify(initial))
  assert.equal(initial.value.hasDocument, false)
  const row = ns => host.context.settings.describe().find(item => item.ns === ns)
  assert.equal(row('locale').value.preference, 'zh')
  assert.equal(row('ui-theme').value.preference, 'system')
  assert.equal(host.assets.loaderStates().filter(item => item.state === 3).length, 0)
  evidence.publicSettingsFormsAndChineseDefault = true
  const theme = await rpc('settings/update', { ns: 'ui-theme', patch: { preference: 'light' }, expectedRevision: row('ui-theme').revision })
  assert.equal(theme.ok, true, JSON.stringify(theme))
  const locale = await rpc('settings/update', { ns: 'locale', patch: { preference: 'en' }, expectedRevision: row('locale').revision })
  assert.equal(locale.ok, true, JSON.stringify(locale))
  assert.equal(row('locale').value.preference, 'en')
  assert.equal(row('locale').base.preference, 'zh')
  assert.equal(row('ui-theme').value.preference, 'light')
  const unknown = await rpc('settings/update', { ns: 'llm-deepseek', patch: { baseURL: 'https://unapproved.invalid' } })
  assert.equal(unknown.ok, false)
  const dangerous = await rpc('settings/update', { ns: 'api-session-controller', patch: { nativeOpen: true } })
  assert.equal(dangerous.ok, false)
  evidence.settingsCannotEnableProviderOrNativeOpen = true
  const credentials = await rpc('credentials/set', { ref: 'unapproved-key', value: 'must-not-be-stored' })
  assert.equal(credentials.ok, false, JSON.stringify(credentials))
  assert.equal((await host.context.credentials.resolve('unapproved-key')), undefined)
  const open = await rpc('settings/openSettingsDocument')
  assert.equal(open.ok, false)
  evidence.nodeCredentialsAndNativeFileOpenRemainUnavailable = true
  assert.equal(host.context.tools.schemas().some(tool => /shell|terminal|filesystem|mcp/u.test(tool.name)), false)
  await peer.dispose()
  await host.dispose()
  await boot()
  assert.equal(row('locale').value.preference, 'en')
  assert.equal(row('ui-theme').value.preference, 'light')
  assert.equal(host.context.sessions.list().length, 0)
  assert.equal(network, 0)
  evidence.officialUserOverridesSurviveRestartWithoutProvider = true
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-settings-controller-probe-v1', status: 'passed', ...evidence }))
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
