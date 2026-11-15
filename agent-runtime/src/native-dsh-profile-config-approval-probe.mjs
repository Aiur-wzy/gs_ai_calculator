/** Real DSH Turns, official approval/audit, owner event stream and ConfigEditor.
 * The provider and human decisions are explicit offline fixtures. No GUI or
 * synthetic Turn is created, and no runtime approval state machine is copied.
 */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'

const base = resolve(process.argv[2] ?? 'build/r14-profile-config-approval-probe')
await mkdir(base, { recursive: true })
const root = await mkdtemp(join(base, 'profile-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
let host, peer, fake, currentMode, input, answerError, response
let closing = false, eventPump, eventsAbort, eventIterator, clientId
const requests = []
globalThis.fetch = (url, options) => {
  if (!String(url).endsWith('/v1/messages')) throw new Error('unexpected_network_in_offline_config_approval_probe')
  return fake(url, options)
}
const assets = { contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
  acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
  localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href }
const rpc = async (endpoint, args = {}) => {
  const received = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: endpoint, payload: { args } }),
  }), peer)
  return (await received.json()).result
}
try {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'), clientAssets: assets })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1,
    secret: 'offline-profile-approval-fixture-not-a-real-key' })
  host.context.tools.register(defineTool({ name: 'fixture_config_request', description: 'Offline exact config approval fixture', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(_args, exec) {
      response = await rpc('genshinProfile/configRequestEdit', { request: input, agentId: exec.agent.id })
      return JSON.stringify(response)
    } }))
  eventsAbort = new AbortController()
  const stream = await host.context.typertGateway.wireStream.open('$events', { args: {} },
    { async *[Symbol.asyncIterator]() {} }, peer, eventsAbort.signal)
  eventIterator = stream[Symbol.asyncIterator]()
  const first = await eventIterator.next()
  assert.equal(first.value.type, 'ready'); clientId = first.value.clientId
  assert.equal(host.context.typertGateway.hasLiveClient(), true)
  eventPump = (async () => {
    while (true) {
      const item = await eventIterator.next()
      if (item.done) return
      const frame = item.value
      if (frame.type !== 'waterfall' || frame.event !== 'approval/request') continue
      requests.push({ mode: currentMode, frame })
      assert.ok(frame.request.reason.includes(input.entry_id))
      assert.ok(frame.request.reason.includes(input.expected_fingerprint))
      assert.match(frame.request.reason, /[0-9a-f]{64}/)
      assert.ok(frame.request.reason.includes(String(input.operations[0].value)))
      if (currentMode === 'stale') {
        const result = await rpc('genshinProfile/configEdit', { request: { ...input,
          operations: [{ op: 'set', path: ['thresholdRatio'], value: 0.85 }] } })
        assert.equal(result.ok, true, JSON.stringify(result))
      }
      const result = await rpc('$events/result', { clientId, eventId: frame.eventId,
        outcome: { kind: 'result', value: currentMode === 'reject' ? 'rejected' : 'allowed-once' } })
      assert.equal(result.ok, true, JSON.stringify(result))
    }
  })().catch(error => { if (!closing) answerError = error })

  for (const mode of ['reject', 'allow', 'stale', 'no_client']) {
    currentMode = mode; response = undefined
    const before = (await rpc('genshinProfile/configDescribe', { request: { entry_id: 'compaction-basic' } })).value.entries[0]
    input = { entry_id: 'compaction-basic', expected_fingerprint: before.fingerprint,
      operations: [{ op: 'set', path: ['thresholdRatio'], value: mode === 'allow' ? 0.81 : 0.82 }] }
    if (mode === 'no_client') {
      closing = true; eventsAbort.abort(); await eventIterator.return(); await eventPump
      assert.equal(host.context.typertGateway.hasLiveClient(), false)
    }
    fake = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'fixture_config_request', arguments: {} }], repeatCalls: false })
    const id = 'profile-approval-' + mode
    assert.equal((await rpc('session/create', { request: { sessionId: id } })).ok, true)
    const agent = host.context.agents.get(id)
    assert.equal((await rpc('session/prompt', { request: { sessionId: id, requestId: randomUUID(), mode: 'queue',
      content: [{ type: 'text', text: 'Offline profile approval fixture ' + mode }] } })).ok, true)
    await agent.whenIdle()
    if (answerError) throw answerError
    assert.ok(response, mode)
    const after = (await rpc('genshinProfile/configDescribe', { request: { entry_id: 'compaction-basic' } })).value.entries[0]
    const log = await host.context.sessionQuery.readSession(id)
    const asked = log.events.filter(event => event.type === 'approval/asked')
    const decided = log.events.filter(event => event.type === 'approval/decided')
    assert.equal(asked.length, mode === 'no_client' ? 0 : 1)
    assert.equal(decided.length, asked.length)
    if (mode === 'allow') {
      assert.equal(response.ok, true, JSON.stringify(response))
      assert.equal(after.raw.thresholdRatio, 0.81)
      assert.ok(requests.at(-1).frame.request.reason.includes(response.value.approval.request_fingerprint))
    } else {
      assert.equal(response.ok, false, JSON.stringify(response))
      if (mode === 'stale') { assert.equal(response.error.code, 'settings/conflict'); assert.equal(after.raw.thresholdRatio, 0.85) }
      else { assert.equal(after.fingerprint, before.fingerprint); assert.equal(response.error.details.approval_outcome, mode === 'reject' ? 'rejected' : 'unavailable') }
    }
  }
  assert.equal(requests.length, 3)
  const evidence = { contract: 'genshin-dsh-profile-config-approval-v1', status: 'passed',
    officialAgentLoop: true, officialApprovalAndAudit: true, authenticatedOwnerEventStream: true,
    explicitFixtureHumanDecisions: true, rejectedDoesNotWrite: true, exactRequestApproved: true,
    editedDuringApprovalRejectsByCAS: true, noClientFailsClosed: true, realGuiTested: false, realModelTested: false }
  await writeFile(join(base, 'receipt.json'), JSON.stringify(evidence, null, 2) + '\n')
  console.log(JSON.stringify(evidence))
} finally {
  closing = true; eventsAbort?.abort(); await eventIterator?.return(); await eventPump
  await peer?.dispose(); await host?.dispose(); globalThis.fetch = originalFetch
}
