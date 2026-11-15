/** True Session Controller lifecycle and cold persistence; no network/provider. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { serverResponseSchema } from '@deepseek-ai/dsh-client-connection'
import { createNativeDshSessionHost, NATIVE_DSH_SESSION_HOST_CONTRACT } from './native-dsh-session-host.mjs'
import { FIXED_ROOT_VIRTUAL_ROOT } from './fixed-root-filesystem-contract.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const originalFetch = globalThis.fetch
const fakeFetch = createPhase5bOfficialFakeFetch({ requireV2Context: false })
globalThis.fetch = fakeFetch
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/session-controller-'))
const mirrorRoot = join(root, 'media')
await mkdir(mirrorRoot)
const media = Buffer.from('private-reviewed-media')
const leaf = `${createHash('sha256').update(media).digest('hex')}.txt`
await writeFile(join(mirrorRoot, leaf), media)
let host
let peer
const evidence = {}
const clientAssets = {
  contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
  acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
  localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href,
}

async function call(endpoint, args) {
  const rpcId = randomUUID()
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId, method: endpoint, payload: { args } }),
  }), peer)
  assert.equal(response.status, 200)
  const envelope = serverResponseSchema.parse(await response.json())
  assert.equal(envelope.rpcId, rpcId)
  assert.equal(envelope.result.ok, true, JSON.stringify(envelope.result))
  return envelope.result.value
}

async function follow(id) {
  const control = new AbortController()
  const source = await host.wireStream.open('session/follow', { args: { request: {
    address: { kind: 'session', sessionId: id }, assistantStream: true,
  } } }, { async *[Symbol.asyncIterator]() {} }, peer, control.signal)
  const iterator = source[Symbol.asyncIterator]()
  const snapshot = (await iterator.next()).value
  assert.equal(snapshot.type, 'snapshot')
  return { iterator, snapshot, close: async () => { control.abort(); await iterator.return?.() } }
}

try {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot, clientAssets })
  peer = host.createPeer()
  assert.equal(host.productionOwner, true)
  assert.equal(host.assets.productionOwner, true)
  assert.equal(host.compositionIdentity.productionOwner, true)
  const { sha256: identityHash, ...identityBody } = host.compositionIdentity
  assert.equal(createHash('sha256').update(JSON.stringify(identityBody)).digest('hex'), identityHash)
  evidence.productionDescriptionMatchesSelectedGraphAndDigest = true
  const manual = await createNativeDshSessionHost({ profileRoot: join(root, 'manual-qualification'), mirrorRoot })
  try {
    assert.equal(manual.productionOwner, false)
    assert.equal(manual.compositionIdentity.productionOwner, false)
    assert.equal(manual.context.sessions.list().length, 0)
  } finally { await manual.dispose() }
  const qualification = await createNativeDshSessionHost({ profileRoot: join(root, 'graph-qualification'), mirrorRoot,
    clientAssets: { ...clientAssets, contract: { ...clientAssets.contract, productionOwner: false } } })
  try {
    assert.equal(qualification.productionOwner, false)
    assert.equal(qualification.assets.productionOwner, false)
    assert.equal(qualification.compositionIdentity.productionOwner, false)
  } finally { await qualification.dispose() }
  await assert.rejects(createNativeDshSessionHost({ profileRoot: join(root, 'invalid-description'), mirrorRoot,
    clientAssets: { ...clientAssets, contract: { ...clientAssets.contract, productionOwner: 'true' } } }),
  /native_dsh_client_assets_contract_invalid/u)
  evidence.qualificationDescriptionStaysSeparateAndWrongTypeRejected = true
  assert.equal(fakeFetch.requestCount, 0)
  assert.equal(fakeFetch.modelListingRequestCount, 0)
  assert.equal(host.context.sessions.list().length, 0)
  const boot = host.assets.boot()
  assert.equal(boot.filter(row => row.kind === 'global' && row.name === '__DSH_BOOT__').length, 1)
  const graph = host.assets.graph()
  assert.equal(graph.entries.length, clientAssets.contract.dynamicModules.length)
  assert.equal(host.assets.loaderStates().filter(row => row.state === 3).length, 0)
  evidence.noFailedLoaderEntries = true
  const script = await host.assets.bundle(graph.entries[0].url)
  assert.equal(script.status, 200)
  assert.ok((await script.text()).length > 0)
  assert.equal((await host.assets.bundle('/plugins/??unreviewed/client.js&rev=000000000000')).status, 404)
  evidence.sameContextReviewedClientComposition = true
  const list = await call('session/list', { _request: {} })
  assert.deepEqual(list.items, [])
  await assert.rejects(host.sessionStatus('unknown-session'), { code: 'SESSION_QUERY_SESSION_NOT_FOUND' })
  evidence.idleBootCreatesNoSessionOrProvider = true
  await host.kernel.secretController.set({ provider: 'deepseek-official',
    credential_generation: 1, secret: 'session-controller-offline-sentinel' })
  const first = await call('session/create', { request: { sessionId: 'session-host-one' } })
  const second = await call('session/create', { request: { sessionId: 'session-host-two' } })
  assert.equal(first.sessionId, 'session-host-one')
  assert.equal(second.sessionId, 'session-host-two')
  assert.equal(host.context.agents.list().length, 2)

  const live = await follow(first.sessionId)
  const seen = []
  const completed = (async () => {
    for await (const frame of { [Symbol.asyncIterator]: () => live.iterator }) {
      seen.push(frame)
      if (frame.type === 'event' && frame.event.type === 'turn/end') return
    }
  })()
  const requestId = randomUUID()
  const accepted = await call('session/prompt', { request: {
    requestId, sessionId: first.sessionId, mode: 'queue',
    content: [{ type: 'text', text: 'SessionController original visible user prompt' }],
    clientTimeZone: 'Asia/Shanghai',
  } })
  assert.equal(accepted.accepted, true)
  await host.context.agents.get(first.sessionId).whenIdle()
  await completed
  await live.close()
  const events = seen.filter(frame => frame.type === 'event').map(frame => frame.event)
  const user = events.find(event => event.type === 'user/message')
  assert.equal(user.data.source.rpcId, requestId)
  assert.equal(user.data.content[0].text, 'SessionController original visible user prompt')
  const answer = events.find(event => event.type === 'assistant/message')
  assert.ok(answer.data.message.content.some(block => block.type === 'text'
    && block.text === 'phase5b official answer'))
  assert.ok(answer.data.message.content.some(block => block.type === 'reasoning'
    && block.text === 'phase5b offline reasoning'))
  assert.ok(seen.some(frame => frame.type === 'assistant-stream'))
  evidence.liveUserReasoningFinalAnswer = true
  evidence.requestCorrelation = true

  await call('session/prompt', { request: {
    requestId: randomUUID(), sessionId: second.sessionId, mode: 'queue',
    content: [{ type: 'text', text: 'Second independent Session' }],
  } })
  await host.context.agents.get(second.sessionId).whenIdle()
  assert.equal(fakeFetch.requestCount, 2)
  let releaseFirst
  const firstHeld = new Promise(resolve => { releaseFirst = resolve })
  let held = false
  globalThis.fetch = async (...args) => {
    if (String(args[0]).endsWith('/v1/messages') && !held) {
      held = true
      await firstHeld
    }
    return fakeFetch(...args)
  }
  try {
    await Promise.all([first, second].map(item => call('session/prompt', { request: {
      requestId: randomUUID(), sessionId: item.sessionId, mode: 'queue',
      content: [{ type: 'text', text: `Concurrent prompt for ${item.sessionId}` }],
    } })))
    const deadline = Date.now() + 5_000
    while (host.admission.snapshot().waiting !== 1 && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 1))
    }
    assert.equal(host.admission.snapshot().waiting, 1)
    assert.equal(host.admission.snapshot().active, 1)
    const statuses = await Promise.all([first, second].map(item => host.sessionStatus(item.sessionId)))
    assert.equal(statuses.filter(item => item.admission.state === 'active').length, 1)
    assert.equal(statuses.filter(item => item.admission.state === 'queued').length, 1)
    assert.ok(statuses.every(item => item.agent_running === true))
  } finally { releaseFirst() }
  await Promise.all([first, second].map(item => host.context.agents.get(item.sessionId).whenIdle()))
  globalThis.fetch = fakeFetch
  assert.equal(fakeFetch.requestCount, 4)
  assert.equal(host.admission.snapshot().peakActive, 1)
  assert.equal(host.admission.snapshot().peakWaiting, 1)
  evidence.multipleDshHandlesProviderCapacityOne = host.admission.snapshot()
  await host.kernel.flushSessions()
  await peer.dispose()
  await host.dispose()
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot, clientAssets })
  peer = host.createPeer()
  const beforeCold = fakeFetch.requestCount
  assert.equal(host.context.agents.list().length, 0)
  const coldStatus = await host.sessionStatus(first.sessionId)
  assert.deepEqual(coldStatus.last_turn, { turn: 2, reason: { kind: 'completed' } })
  assert.equal(coldStatus.agent_running, null)
  assert.equal(coldStatus.admission.state, 'idle')
  assert.equal(host.context.agents.list().length, 0)
  evidence.coldLastTurnReadDoesNotActivateAgent = true
  const cold = await call('session/list', { _request: {} })
  assert.equal(cold.items.length, 2)
  assert.ok(cold.items.every(item => item.agentAvailable === false))
  assert.equal(host.context.agents.list().length, 0)
  const reopened = await follow(first.sessionId)
  const page = await call('session/page', { request: {
    address: { kind: 'session', sessionId: first.sessionId },
    throughSeq: reopened.snapshot.cursor,
  } })
  const text = JSON.stringify(page.records)
  assert.ok(text.includes('SessionController original visible user prompt'))
  assert.ok(text.includes('phase5b official answer'))
  assert.ok(text.includes('phase5b offline reasoning'))
  await reopened.close()
  assert.equal(fakeFetch.requestCount, beforeCold)
  evidence.coldListPageFollowRestoresDurableConversation = true

  const read = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/file?path=${
    encodeURIComponent(`${FIXED_ROOT_VIRTUAL_ROOT}/${leaf}`)}`), peer)
  assert.equal(read.status, 200)
  assert.deepEqual(Buffer.from(await read.arrayBuffer()), media)
  const escape = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/file?path=${
    encodeURIComponent(join(root, 'session-query.sqlite'))}`), peer)
  assert.equal(escape.status, 403)
  evidence.controllerFileUsesFixedRoot = true
  evidence.providerRequests = fakeFetch.requestCount
  process.stdout.write(`${JSON.stringify({ contract: NATIVE_DSH_SESSION_HOST_CONTRACT,
    status: 'passed', qualificationOnly: true, ...evidence }, null, 2)}\n`)
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
