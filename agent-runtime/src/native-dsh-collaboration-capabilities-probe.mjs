/** Ordinary approval, durable feedback/references, titles and scoped attachments. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { DEEPSEEK_OFFICIAL_MESSAGES_ROOT } from './native-deepseek-kernel.mjs'

assertPinnedNodeVersion()
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/collaboration-capabilities-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
const fakeFetch = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'fixture_approval', arguments: {} }] })
const outboundURLs = []
globalThis.fetch = (url, init) => { outboundURLs.push(String(url)); return fakeFetch(url, init) }
let host, peer
let approvalMode = 'missing'
const outcomes = []
const evidence = {}
async function boot() {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media') })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'collaboration-offline-fixture' })
}
async function rpc(endpoint, request) {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: `session/${endpoint}`,
      payload: { args: { request } } }),
  }), peer)
  const result = (await response.json()).result
  assert.equal(result.ok, true, JSON.stringify(result))
  return result.value
}
try {
  await boot()
  assert.equal(host.context.attachments.root, join(root, 'dsh-home', 'attachments', 'v1'))
  host.context.tools.register(defineTool({ name: 'fixture_approval', description: 'Only fixture permission request',
    parameters: {}, execute: async (_args, exec) => {
      const control = new AbortController()
      const request = { agent: exec.agent, toolName: 'fixture_approval', callId: exec.callId,
        reason: 'One fixture operation', signal: approvalMode === 'cancel' ? control.signal : exec.signal }
      if (approvalMode === 'cancel') queueMicrotask(() => control.abort())
      const value = await host.context.approval.request(request)
      outcomes.push(value)
      return value
    }, output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
  }))
  host.context.on('approval/request', async (_request, next) => {
    if (approvalMode === 'missing') return next()
    if (approvalMode === 'throw') throw new Error('Fixture answerer failed')
    if (approvalMode === 'cancel') return new Promise(resolve => setTimeout(() => resolve('allowed-once'), 10))
    return approvalMode
  }, { prepend: true })
  for (const mode of ['missing', 'throw', 'rejected', 'allowed-once', 'cancel']) {
    approvalMode = mode
    const id = `collaboration-${mode}`
    await rpc('create', { sessionId: id })
    await rpc('prompt', { sessionId: id, requestId: randomUUID(), mode: 'queue',
      content: [{ type: 'text', text: `Source user visible ${mode}` }] })
    await host.context.agents.get(id).whenIdle()
    const expected = { missing: 'unavailable', throw: 'unavailable', cancel: 'cancelled' }[mode] ?? mode
    assert.equal(outcomes.at(-1), expected)
    const log = await host.context.sessionQuery.readSession(id)
    const asked = log.events.filter(event => event.type === 'approval/asked')
    const decided = log.events.filter(event => event.type === 'approval/decided')
    assert.equal(asked.length, 1)
    assert.equal(decided.length, 1)
    assert.equal(asked[0].data.id, decided[0].data.id)
    assert.equal(decided[0].data.outcome, expected)
  }
  evidence.ordinaryApprovalOneShotFailClosedCancelDurableAudit = true
  assert.equal(host.context.tools.get('gs_patch_candidate'), undefined)
  assert.equal(host.domainTools, undefined)
  evidence.ordinaryApprovalCannotMintAppToolsOrDomainAuthority = true
  const id = 'collaboration-allowed-once'
  const log = await host.context.sessionQuery.readSession(id)
  const answer = log.events.findLast(event => event.type === 'assistant/message')
  const messageId = answer.data.message.id
  const request = { sessionId: id, messageId, rating: 'positive', note: '  Preserve note verbatim  ', ifVersion: null }
  const put = await host.context.messageFeedback.put(request)
  assert.equal(put.ok, true, JSON.stringify(put))
  const unchanged = await host.context.messageFeedback.put({ ...request, ifVersion: put.value.version })
  assert.equal(unchanged.value.version, put.value.version)
  const stale = await host.context.messageFeedback.put({ ...request, rating: 'negative' })
  assert.equal(stale.error.code, 'version-conflict')
  const oversized = await host.context.messageFeedback.put({ ...request, note: 'x'.repeat(8193) })
  assert.equal(oversized.error.code, 'note-too-large')
  evidence.feedbackCompareAndSetNoopAndOfficial8192BytePolicy = true
  const other = host.context.agents.get('collaboration-missing')
  const referenceRoute = { provider: 'deepseek-official', model: 'deepseek-flash' }
  const referenced = await host.context.sessionReferenceResolver.prepare(other,
    [{ type: 'text', text: 'Read explicitly selected source' }], [{ sessionId: id }], referenceRoute)
  assert.equal(referenced.additionalContext.source.kind, 'session-reference')
  const content = JSON.stringify(referenced.additionalContext)
  assert.ok(content.includes('Source user visible allowed-once'))
  assert.ok(content.includes('phase5b official answer'))
  assert.equal(content.includes('phase5b offline reasoning'), false)
  assert.equal(content.includes('Preserve note verbatim'), false)
  await assert.rejects(host.context.sessionReferenceResolver.prepare(other, [], [{ sessionId: other.id }], referenceRoute),
    error => error.code === 'SESSION_REFERENCE_SELF_REFERENCE')
  evidence.referencesUseVisibleConversationNotReasoningOrFeedback = true
  const bytes = Buffer.from('User attachment\n中文\0exact bytes')
  const upload = await host.context.fileUploads.uploadStream({ sessionId: id, name: '../source.txt',
    data: { async *[Symbol.asyncIterator]() { yield bytes.subarray(0, 4); yield bytes.subarray(4) } } })
  assert.deepEqual(host.context.fileUploads.resolve(host.context.agents.get(id), upload.receiptId),
    { type: 'file', attachment: upload.file })
  assert.equal(host.context.fileUploads.resolve(other, upload.receiptId), undefined)
  const chunks = []
  for await (const chunk of host.context.attachments.readFileStream(upload.file)) chunks.push(chunk)
  assert.deepEqual(Buffer.concat(chunks), bytes)
  evidence.attachmentsStayInExplicitProfileWithoutAmbientDshHome = true
  evidence.streamedAttachmentIntegrityAndSessionReceiptIsolation = true
  const title = host.context.sessionTitle.rename(host.context.agents.get(id).session, 'User selected durable title')
  assert.equal(title.title, 'User selected durable title')
  const requestsBeforeRestart = fakeFetch.requestCount
  await peer.dispose()
  await host.dispose()
  await boot()
  assert.equal(fakeFetch.requestCount, requestsBeforeRestart)
  assert.equal(host.context.agents.list().length, 0)
  const restored = await host.context.messageFeedback.list({ sessionId: id })
  assert.equal(restored.value.items[0].note, request.note)
  const deleted = await host.context.messageFeedback.delete({ sessionId: id, messageId, ifVersion: put.value.version })
  assert.equal(deleted.ok, true)
  assert.deepEqual((await host.context.messageFeedback.list({ sessionId: id })).value.items, [])
  assert.equal(host.context.agents.list().length, 0)
  const cold = await host.context.sessionQuery.readSession(id)
  assert.equal(cold.events.findLast(event => event.type === 'session/title').data.title, title.title)
  assert.ok(cold.events.some(event => event.type === 'feedback/message-delete'))
  assert.equal(fakeFetch.requestCount, requestsBeforeRestart)
  assert.ok(outboundURLs.every(url => url === 'https://api.deepseek.com/models'
    || url === `${DEEPSEEK_OFFICIAL_MESSAGES_ROOT}/v1/messages`), JSON.stringify(outboundURLs))
  evidence.feedbackAndReferenceDoNotTriggerTelemetryOrModelCalls = true
  evidence.coldFeedbackTitleRestoreWithoutWakingAgentOrProvider = true
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-collaboration-capabilities-probe-v1', status: 'passed', ...evidence }))
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
