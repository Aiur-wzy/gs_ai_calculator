/** D04 B and durable Goal/Plan through actual DSH loop and official adapter. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/generic-capabilities-'))
const mirrorRoot = join(root, 'media')
await mkdir(mirrorRoot)
const originalFetch = globalThis.fetch
let host
let peer
let mode = 'success'
let requests = 0
let settledCalls = 0
const decisions = []
const fallback = createPhase5bOfficialFakeFetch({ requireV2Context: false })

function sse(events) {
  return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
}
function failedStream(kind) {
  const block = kind === 'tool' ? { type: 'tool_use', id: 'fixture-proposal', name: 'gs_fixture_settle', input: {} }
    : kind === 'reasoning' ? { type: 'thinking', thinking: '' } : { type: 'text', text: '' }
  const delta = kind === 'reasoning' ? { type: 'thinking_delta', thinking: 'Visible reasoning before failure' }
    : { type: 'text_delta', text: 'Visible text before failure' }
  return sse([
    { type: 'message_start', message: { usage: { input_tokens: 1, output_tokens: 0 } } },
    { type: 'content_block_start', index: 0, content_block: block },
    ...(kind === 'tool' ? [] : [{ type: 'content_block_delta', index: 0, delta }]),
    { type: 'error', error: { type: 'overloaded_error', message: 'Fixture temporary error' } },
  ])
}
function settledTool() {
  return sse([
    { type: 'message_start', message: { usage: { input_tokens: 1, output_tokens: 0 } } },
    { type: 'content_block_start', index: 0,
      content_block: { type: 'tool_use', id: 'fixture-settled', name: 'gs_fixture_settle', input: {} } },
    { type: 'content_block_stop', index: 0 },
    { type: 'message_delta', delta: { stop_reason: 'tool_use' }, usage: { output_tokens: 1 } },
    { type: 'message_stop' },
  ])
}
globalThis.fetch = async (url, init) => {
  if (!String(url).endsWith('/v1/messages')) return fallback(url, init)
  requests += 1
  if ((mode === 'empty' || mode === 'unknown' || mode === 'cancel') && requests === 1
      || mode === 'exhaust' || mode === 'settled' && requests === 2) {
    return Response.json({ error: { type: 'server_error', message: 'Fixture temporary error' } }, { status: 503 })
  }
  if (['text', 'reasoning', 'tool'].includes(mode)) return failedStream(mode)
  if (mode === 'settled' && requests === 1) return settledTool()
  return fallback(url, init)
}

async function call(endpoint, request) {
  const rpcId = randomUUID()
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId, method: `session/${endpoint}`,
      payload: { args: { request } } }),
  }), peer)
  const envelope = await response.json()
  assert.equal(envelope.result.ok, true, JSON.stringify(envelope.result))
  return envelope.result.value
}
async function create(id) {
  await call('create', { sessionId: id })
  return host.context.agents.get(id)
}
async function prompt(id) {
  return call('prompt', { sessionId: id, requestId: randomUUID(), mode: 'queue',
    content: [{ type: 'text', text: `Generic capability fixture ${mode}` }] })
}
async function waitFor(predicate) {
  const deadline = Date.now() + 5_000
  while (!predicate() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 1))
  assert.ok(predicate(), 'fixture condition not reached')
}

const evidence = {}
try {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot,
    providerRetryPolicy: { mode: 'normal', maxRetries: 2,
      backoff: { initialDelayMs: 10, maxDelayMs: 10, jitterRatio: 0 } },
    retryAdmission: {
      canRetryDomainAttempt: async () => mode !== 'unknown',
      onDecision: decision => decisions.push(decision),
    },
  })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official',
    credential_generation: 1, secret: 'generic-capability-offline-sentinel' })
  host.context.tools.register(defineTool({
    name: 'gs_fixture_settle', description: 'Settle one offline fixture operation', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    execute() { settledCalls += 1; return 'settled once' },
  }))
  for (const testMode of ['empty', 'text', 'reasoning', 'tool', 'unknown', 'settled', 'exhaust']) {
    mode = testMode
    requests = 0
    decisions.length = 0
    const id = `generic-${mode}`
    const agent = await create(id)
    const baseline = host.admission.snapshot().admittedAttempts
    await prompt(id)
    await agent.whenIdle().catch(() => {})
    const log = await host.context.sessionQuery.readSession(id)
    const retries = log.events.filter(event => event.type === 'llm/retry')
    const expected = { empty: 2, text: 1, reasoning: 1, tool: 1, unknown: 1, settled: 3, exhaust: 3 }[mode]
    assert.equal(requests, expected, mode)
    assert.equal(host.admission.snapshot().admittedAttempts - baseline, expected, `${mode} budget accounting`)
    assert.equal(retries.length, expected - (mode === 'settled' ? 2 : 1), mode)
    if (['text', 'reasoning', 'tool'].includes(mode)) {
      assert.equal(decisions.at(-1).reason, 'current_attempt_output_accepted')
      assert.equal(settledCalls, 0)
    }
    if (mode === 'unknown') assert.equal(decisions.at(-1).reason, 'domain_outcome_unsettled')
    if (mode === 'settled') assert.equal(settledCalls, 1)
    evidence[mode] = { attempts: requests, retries: retries.length }
  }

  mode = 'cancel'
  requests = 0
  const cancelling = await create('generic-cancel')
  let scheduled = false
  const unwatch = host.context.on('session/event', (session, event) => {
    if (session.id === cancelling.id && event.type === 'llm/retry') scheduled = true
  })
  await prompt(cancelling.id)
  await waitFor(() => scheduled)
  await call('cancel', { sessionId: cancelling.id })
  await cancelling.whenIdle().catch(() => {})
  unwatch()
  assert.equal(requests, 1)
  evidence.cancelDuringBackoff = true

  mode = 'success'
  const durable = await create('generic-durable')
  assert.equal(host.context.planMode.set(durable, true), 'committed')
  const goal = host.context.goals.create(durable, { objective: 'Explicit fixture goal', maxGoalRounds: 2 })
  host.context.goals.disarm(durable)
  const countBeforeRestart = requests
  await host.kernel.flushSessions()
  await peer.dispose()
  await host.dispose()
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot })
  peer = host.createPeer()
  const restored = await host.context.sessionController.resolveAgent(durable.id)
  assert.ok(restored.agent)
  const restoredGoal = host.context.goals.get(restored.agent)
  assert.equal(restoredGoal.id, goal.id)
  assert.equal(restoredGoal.activation, 'disarmed')
  assert.equal(host.context.planMode.get(restored.agent).active, true)
  assert.equal(requests, countBeforeRestart)
  evidence.goalPlanDurableAndRestartDisarmed = true
  process.stdout.write(`${JSON.stringify({ contract: 'genshin-native-dsh-generic-capabilities-v1',
    status: 'passed', productionOwner: false, ...evidence }, null, 2)}\n`)
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
