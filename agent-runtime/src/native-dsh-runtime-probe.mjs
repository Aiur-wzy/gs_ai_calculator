/** Production runner over an actual named pipe, with an offline provider only in this probe. */
import assert from 'node:assert/strict'
import { createServer, connect } from 'node:net'
import { once } from 'node:events'
import { createHash, randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, readdir, readFile, rm } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'
import { readNativeDshRuntimeConfig, NATIVE_DSH_RUNTIME_CONTROL_CONTRACT } from './native-dsh-runtime-config.mjs'
import { runNativeDshRuntime, nativeDshRuntimeErrorCode } from './native-dsh-runtime.mjs'
import { decodeDshFetchPayload } from './native-dsh-mux-gateway.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
assert.equal(nativeDshRuntimeErrorCode(new TypeError('fixed_root_filesystem_root_invalid')), 'native_dsh_fixed_root_filesystem_root_invalid')
assert.equal(nativeDshRuntimeErrorCode(new Error('private/path/sk-secret-sentinel')), 'native_dsh_runtime_failed')
const originalFetch = globalThis.fetch
const fakeFetch = createPhase5bOfficialFakeFetch({ requireV2Context: false })
globalThis.fetch = fakeFetch
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/dsh-runtime-'))
const mirrorRoot = join(root, 'media')
await mkdir(mirrorRoot)
const assetsRoot = resolve('frontend')
const sentinel = 'runtime-probe-ephemeral-key-sentinel'
const server = createServer()
const pipeName = `\\\\.\\pipe\\genshin-runtime-probe-${randomUUID()}`
server.listen(pipeName)
await once(server, 'listening')
const accepted = once(server, 'connection')
const hostSocket = connect(pipeName)
await once(hostSocket, 'connect')
const [runtimeSocket] = await accepted
const identity = { connectionGeneration: 1, connectionId: 'production-runner-probe', runtimeIncarnation: 'runner' }
const host = new NativeMuxPipeClient(hostSocket, { senderRole: 'host', peerSenderRole: 'runtime', ...identity })
const carrier = new NativeMuxPipeClient(runtimeSocket, { senderRole: 'runtime', peerSenderRole: 'host', ...identity })
let running
async function control(operation, body = {}, kind = 'dsh_runtime_control_result') {
  const correlation = randomUUID()
  host.enqueue({ lane: 'control', payloadKind: operation, payload: Buffer.from(JSON.stringify(body)),
    messageId: randomUUID(), correlationId: correlation, trustedClass: 'control' })
  await host.writeNext()
  const packet = await host.readNext({ idleTimeoutMs: 20_000 })
  host.settleRead('accepted')
  assert.equal(packet.header.correlation_id, correlation)
  assert.equal(packet.header.payload_kind, kind)
  const value = JSON.parse(packet.payload)
  assert.equal(JSON.stringify(value).includes(sentinel), false)
  if (kind.endsWith('_result')) assert.equal(value.contract, NATIVE_DSH_RUNTIME_CONTROL_CONTRACT)
  return value
}
const evidence = {}
try {
  for (const invalid of [
    ['relative-root', mirrorRoot, assetsRoot],
    [root, mirrorRoot, assetsRoot, JSON.stringify({ secret: sentinel })],
    [root, mirrorRoot, assetsRoot, JSON.stringify({ endpoint: 'https://unreviewed.invalid' })],
    [root, mirrorRoot, assetsRoot, JSON.stringify({ model: 'unreviewed-model' })],
    [root, mirrorRoot, assetsRoot, JSON.stringify({ providerRetryPolicy: { mode: 'off' } })],
  ]) await assert.rejects(readNativeDshRuntimeConfig(invalid))
  const config = await readNativeDshRuntimeConfig([root, mirrorRoot, assetsRoot])
  assert.deepEqual(config.dshSettings, {})
  assert.deepEqual(config.providerRetryPolicy, { mode: 'normal' })
  assert.equal(config.providerCapacity, 2)
  assert.equal((await readNativeDshRuntimeConfig([root, mirrorRoot, assetsRoot,
    JSON.stringify({ providerCapacity: 1 })])).providerCapacity, 1)
  running = runNativeDshRuntime({ carrier, config })
  const initial = (await control('dsh_runtime_status')).result
  assert.equal(initial.credentials.configured, false)
  assert.equal(initial.provider_admission.admittedAttempts, 0)
  assert.equal(fakeFetch.requestCount, 0)
  assert.equal(fakeFetch.modelListingRequestCount, 0)
  assert.equal(initial.composition_identity.appDomainTools, 'public-agent-created-scoped-v1')
  assert.equal(initial.composition_identity.productionOwner, true)
  const { sha256: identityHash, ...identityBody } = initial.composition_identity
  assert.equal(createHash('sha256').update(JSON.stringify(identityBody)).digest('hex'), identityHash)
  evidence.actualControlReportsProductionDescriptionAndValidDigest = true
  assert.ok(initial.composition_identity.clientModules.length >= 27)
  evidence.idleReadyNoCredentialsNoSessionNoProvider = true
  const absent = await control('dsh_runtime_session_status', { session_id: 'absent' }, 'dsh_runtime_control_error')
  assert.equal(absent.code, 'SESSION_QUERY_SESSION_NOT_FOUND')
  await control('dsh_runtime_session_status', {}, 'dsh_runtime_control_error')
  await control('dsh_runtime_shutdown', { invalid: true }, 'dsh_runtime_control_error')
  assert.equal((await control('dsh_runtime_status')).result.credentials.configured, false)
  evidence.badControlDoesNotShutDownRuntime = true
  const set = await control('dsh_secret_set', { provider: 'deepseek-official', credential_generation: 1, secret: sentinel })
  assert.equal(set.result.disposition, 'synchronized')
  const replay = await control('dsh_secret_set', { provider: 'deepseek-official', credential_generation: 1, secret: sentinel })
  assert.equal(replay.result.disposition, 'synchronized')
  await control('dsh_secret_clear', { provider: 'deepseek-official', credential_generation: 2 })
  const rejected = await control('dsh_secret_set', { provider: 'deepseek-official', credential_generation: 1, secret: sentinel }, 'dsh_runtime_control_error')
  assert.equal(rejected.code, 'secret_generation_stale')
  assert.equal((await control('dsh_runtime_status')).result.credentials.configured, false)
  evidence.ephemeralCredentialsExactGenerationNoSecretInReplies = true
  const correlation = randomUUID()
  host.enqueue({ lane: 'bundle', payloadKind: 'dsh_boot_request', payload: Buffer.from('{}'),
    messageId: randomUUID(), correlationId: correlation })
  await host.writeNext()
  let packet = await host.readNext({ idleTimeoutMs: 10_000 })
  host.settleRead('accepted')
  assert.equal(packet.header.payload_kind, 'dsh_boot_result')
  const injections = JSON.parse(packet.payload).injections
  assert.ok(injections.length > 0)
  const preload = injections.find(row => row.kind === 'script-src' && typeof row.src === 'string')
  assert.ok(preload)
  host.enqueue({ lane: 'bundle', payloadKind: 'dsh_bundle_request', payload: Buffer.from(JSON.stringify({ url: preload.src })),
    messageId: randomUUID(), correlationId: randomUUID() })
  await host.writeNext()
  packet = await host.readNext({ idleTimeoutMs: 10_000 })
  host.settleRead('accepted')
  assert.equal(packet.header.payload_kind, 'dsh_bundle_result')
  const bundle = decodeDshFetchPayload(packet.payload)
  assert.equal(bundle.metadata.status, 200)
  assert.ok(bundle.body.length > 0)
  evidence.sameRuntimeBootAndBundlePublicRegistry = true
  await control('dsh_runtime_shutdown', {}, 'dsh_runtime_control_error')
  const drain = await control('dsh_runtime_drain')
  assert.equal(drain.result.state, 'drained')
  assert.equal(drain.result.provider_admission.active, 0)
  await control('dsh_runtime_shutdown')
  await running
  const files = await readdir(root, { recursive: true, withFileTypes: true })
  for (const file of files.filter(row => row.isFile())) {
    const bytes = await readFile(join(file.parentPath, file.name))
    assert.equal(bytes.includes(Buffer.from(sentinel)), false)
  }
  assert.equal(fakeFetch.requestCount, 0)
  evidence.noCredentialOnDiskAndNoModelTaskAtBoot = true
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-runtime-probe-v1', status: 'passed', ...evidence }))
} finally {
  carrier.close()
  host.close()
  await running?.catch(() => {})
  await new Promise(resolve => server.close(resolve))
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
