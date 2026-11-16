/** Offline full-profile ConfigEditor Remote, ordinary fields and cold replay. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'

const base = resolve(process.argv[2] ?? 'build/r14-full-config-editor-probe')
await mkdir(base, { recursive: true })
const root = await mkdtemp(join(base, 'profile-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
let network = 0, host, peer
globalThis.fetch = () => { network++; throw new Error('No network in configuration qualification') }
const assets = { contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
  acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
  localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href }
const boot = async () => {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'), clientAssets: assets })
  peer = host.createPeer()
}
const rpc = async (name, request) => {
  const endpoint = `genshinProfile/${name}`
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: endpoint, payload: { args: { request } } }),
  }), peer)
  return (await response.json()).result
}
try {
  await boot()
  let described = await rpc('configDescribe', {})
  assert.equal(described.ok, true, JSON.stringify(described))
  assert.equal(described.value.owner, 'dsh_config_editor')
  const compaction = described.value.entries.find(row => row.entry_id === 'compaction-basic')
  assert.ok(compaction?.editable_json)
  assert.ok(compaction.schema)
  assert.equal(host.context.settings.describe().some(row => row.ns === 'compaction-basic'), false)
  let result = await rpc('configEdit', { entry_id: 'compaction-basic', expected_fingerprint: compaction.fingerprint,
    operations: [{ op: 'set', path: ['thresholdRatio'], value: 0.87 }] })
  assert.equal(result.ok, true, JSON.stringify(result))
  const after = result.value.entries[0]
  assert.equal(after.raw.thresholdRatio, 0.87)
  assert.notEqual(after.fingerprint, compaction.fingerprint)
  result = await rpc('configEdit', { entry_id: 'compaction-basic', expected_fingerprint: compaction.fingerprint,
    operations: [{ op: 'set', path: ['thresholdRatio'], value: 0.88 }] })
  assert.equal(result.ok, false)
  assert.equal(result.error.code, 'settings/conflict')
  result = await rpc('configEdit', { entry_id: 'compaction-basic', expected_fingerprint: after.fingerprint,
    operations: [{ op: 'set', path: ['thresholdRatio'], value: 2 }] })
  assert.equal(result.ok, false)
  described = await rpc('configDescribe', { entry_id: 'compaction-basic' })
  assert.equal(described.value.entries[0].fingerprint, after.fingerprint)
  result = await rpc('configEdit', { entry_id: 'compaction-basic', expected_fingerprint: after.fingerprint,
    operations: [{ op: 'set', path: ['retainRatio'], value: 0.2 }] })
  assert.equal(result.ok, true, JSON.stringify(result))
  assert.equal(result.value.entries[0].raw.retainRatio, 0.2)
  const provider = (await rpc('configDescribe', { entry_id: 'llm-deepseek' })).value.entries[0]
  result = await rpc('configEdit', { entry_id: 'llm-deepseek', expected_fingerprint: provider.fingerprint,
    operations: [{ op: 'set', path: ['baseURL'], value: 'https://user-configured.invalid' }] })
  assert.equal(result.ok, true)
  await peer.dispose(); await host.dispose(); peer = undefined; host = undefined
  await boot()
  described = await rpc('configDescribe', { entry_id: 'compaction-basic' })
  assert.equal(described.value.entries[0].raw.thresholdRatio, 0.87)
  assert.equal(described.value.entries[0].raw.retainRatio, 0.2)
  assert.equal(network, 0)
  const evidence = { contract: 'genshin-dsh-profile-config-qualification-v1', status: 'passed',
    fullConfigNotOnlyLiveFields: true, officialSchemaValidation: true, staleEditRejected: true,
    rejectedWriteDoesNotDrift: true, validEditAfterRejectedLifecycle: true,
    officialProviderCustomEndpointAccepted: true, coldReplay: true, networkCalls: 0 }
  await writeFile(join(base, 'receipt.json'), JSON.stringify(evidence, null, 2) + '\n')
  console.log(JSON.stringify(evidence))
} finally {
  await peer?.dispose(); await host?.dispose(); globalThis.fetch = originalFetch
}
