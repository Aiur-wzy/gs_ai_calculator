/** Offline public Settings/LLM/credential qualification with synthetic keys. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, readFile, readdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'

const base = resolve(process.argv[2] ?? '.codex-tmp/provider-extension')
await mkdir(base, { recursive: true })
const root = await mkdtemp(join(base, 'profile-'))
const originalFetch = globalThis.fetch
let network = 0, host, peer
globalThis.fetch = () => { network++; throw new Error('offline qualification forbids network') }
const boot = async () => {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media') })
  peer = host.createPeer()
}
const rpc = async (endpoint, args = {}) => {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: endpoint, payload: { args } }),
  }), peer)
  return (await response.json()).result
}
const profile = (method, request) => rpc(`genshinProfile/${method}`, { request })
const evidence = {}
try {
  await boot()
  assert.equal(host.kernel.fullProfile.rows().find(row => row.id === 'llm-pi-ai').state, 2)
  const directory = host.context.llm.listConfigurableProviders()
  assert.ok(directory.some(row => row.provider === 'openai'))
  assert.ok(directory.some(row => row.provider === 'anthropic'))
  assert.ok(directory.some(row => row.provider === 'google'))
  evidence.upstreamDirectoryEnabled = directory.length
  const set = await rpc('credentials/set', { ref: 'QUALIFICATION_ROUTE_KEY', value: 'synthetic-key-for-offline-test' })
  assert.equal(set.ok, true, JSON.stringify(set))
  const described = await rpc('credentials/describe', { refs: ['QUALIFICATION_ROUTE_KEY', 'UNCONFIGURED_ROUTE_KEY'] })
  assert.equal(described.ok, true)
  assert.equal(described.value.QUALIFICATION_ROUTE_KEY.configured, true)
  assert.equal(described.value.UNCONFIGURED_ROUTE_KEY.configured, false)
  assert.ok(!JSON.stringify(described).includes('synthetic-key-for-offline-test'))
  evidence.credentialViewsNeverRevealValue = true
  const models = (await profile('configDescribe', { entry_id: 'llm-pi-ai' })).value.entries[0]
  const changed = await profile('configEdit', { entry_id: 'llm-pi-ai', expected_fingerprint: models.fingerprint,
    operations: [{ op: 'set', path: ['providers'], value: { 'offline-compat': {
      api: 'openai-responses', baseURL: 'https://qualification.invalid/v1', apiKeyEnv: 'QUALIFICATION_ROUTE_KEY',
      models: [{ id: 'qualification-model', contextWindow: 200000, maxTokens: 4000 }],
    } } }] })
  assert.equal(changed.ok, true, JSON.stringify(changed))
  const listed = await host.context.llm.listModels('offline-compat')
  assert.equal(listed[0].id, 'qualification-model')
  evidence.customRouteUsesUpstreamModelCatalog = true
  const accepted = changed.value.entries[0]
  const invalid = await profile('configEdit', { entry_id: 'llm-pi-ai', expected_fingerprint: accepted.fingerprint,
    operations: [{ op: 'set', path: ['providers', 'offline-compat', 'api'], value: 'not-a-supported-protocol' }] })
  assert.equal(invalid.ok, false)
  assert.equal((await profile('configDescribe', { entry_id: 'llm-pi-ai' })).value.entries[0].fingerprint,
    accepted.fingerprint)
  evidence.officialValidationRollback = true
  const key = credentialKey('qualification', 'record')
  await host.context.credentials.modifyRecord(key, () => ({ kind: 'grant', payload: { counter: 0 } }))
  await Promise.all(Array.from({ length: 12 }, () => host.context.credentials.modifyRecord(key,
    current => ({ kind: 'grant', payload: { counter: current.payload.counter + 1 } }))))
  assert.equal((await host.context.credentials.readRecord(key)).payload.counter, 12)
  assert.ok((await host.context.credentials.listRecords()).some(row => row.key === key))
  evidence.serializedAuthorizationRecordUpdates = true
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 4, secret: 'legacy-synthetic' })
  await host.context.credentials.set('DEEPSEEK_API_KEY', 'new-generic-synthetic')
  await peer.dispose(); await host.dispose()
  await boot()
  assert.equal((await host.context.credentials.resolve('QUALIFICATION_ROUTE_KEY')).value, 'synthetic-key-for-offline-test')
  assert.equal((await host.context.credentials.readRecord(key)).payload.counter, 12)
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 4, secret: 'legacy-synthetic' })
  assert.equal((await host.context.credentials.resolve('DEEPSEEK_API_KEY')).value, 'new-generic-synthetic')
  await host.context.credentials.unset('DEEPSEEK_API_KEY')
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 4, secret: 'legacy-synthetic' })
  assert.equal(await host.context.credentials.resolve('DEEPSEEK_API_KEY'), undefined)
  await host.context.credentials.deleteRecord(key)
  assert.equal(await host.context.credentials.readRecord(key), undefined)
  evidence.coldRecoveryAndNoLegacyResurrection = true
  assert.equal((await host.context.llm.listModels('offline-compat'))[0].id, 'qualification-model')
  for (const name of await readdir(join(root, 'credentials'))) {
    const bytes = await readFile(join(root, 'credentials', name))
    assert.equal(bytes.includes(Buffer.from('synthetic')), false)
  }
  evidence.encryptedAtRest = true
  assert.equal(network, 0)
  console.log(JSON.stringify({ contract: 'genshin-provider-extension-qualification-v1', status: 'passed',
    ...evidence, networkCalls: network }))
} finally {
  await peer?.dispose(); await host?.dispose(); globalThis.fetch = originalFetch
}
