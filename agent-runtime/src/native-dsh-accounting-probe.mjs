/** Real DSH main/retry/Web scopes with an offline Host accounting peer. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { NativeDshDomainTransport } from './native-dsh-domain-transport.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'
import { canonicalEnvelopeJson } from './bridge-envelope.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'

assertPinnedNodeVersion()
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/dsh-accounting-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
const fake = createNativeDshDomainFixtureFetch({ calls: [
  { tool: 'web_search', arguments: { queries: ['工程角色技能周期资料'] } },
] })
let outboundSearches = 0
let mode = 'web'
let providerRequests = 0
let concurrentRequests = 0
let releaseConcurrent
const concurrentBarrier = new Promise(resolve => { releaseConcurrent = resolve })
const simple = createPhase5bOfficialFakeFetch({ requireV2Context: false })
globalThis.fetch = async (url, init) => {
  if (String(url) === 'https://api.deepseek.com/v1/tools/search') {
    outboundSearches += 1
    return Response.json({ results: [{ url: 'https://example.test/source', title: 'Offline source',
      snippet: 'Research fixture evidence', date: '2026-11-05' }] })
  }
  if (String(url).endsWith('/v1/messages')) {
    providerRequests += 1
    if (mode === 'retry' && providerRequests === 1) {
      return Response.json({ error: { type: 'server_error', message: 'Offline temporary failure' } }, { status: 503 })
    }
    if (mode === 'cancel') {
      await new Promise((_resolve, reject) => {
        const abort = () => reject(new DOMException('Aborted', 'AbortError'))
        if (init.signal.aborted) abort()
        else init.signal.addEventListener('abort', abort, { once: true })
      })
    }
    if (mode === 'concurrent') {
      concurrentRequests += 1
      await concurrentBarrier
    }
    if (mode !== 'web') return simple(url, init)
  }
  return fake(url, init)
}
const calls = []
const registrations = []
const settles = []
const leases = []
const budgetSettles = []
let transport
let denyResearch = false
let denyBudget = false
transport = new NativeDshDomainTransport({ send(packet) {
  const value = JSON.parse(packet.payload)
  calls.push({ kind: packet.payloadKind, value })
  let kind, result, contract
  if (packet.payloadKind === 'dsh_app_bind_request') {
    kind = 'dsh_app_bind_result'
    transport.accept({ header: { lane: 'remote_unary', payload_kind: kind, correlation_id: packet.correlationId },
      payload: Buffer.from(JSON.stringify({ contract: 'genshin-native-dsh-domain-bound-v1',
        request_id: value.request_id, session_id: value.session_id, binding_state: 'unbound', snapshot: null,
        runtime_context: {}, prompt_bundle: { system_prompt: 'Offline research fixture', required_skill_resources: [] } })) })
    return
  }
  if (packet.payloadKind === 'dsh_budget_acquire_request') {
    leases.push(value)
    kind = 'dsh_budget_acquire_result'
    contract = 'genshin-native-dsh-budget-result-v1'
    result = { allowed: !denyBudget, lease_id: value.lease_id,
      ...(denyBudget ? { reason: { code: 'fixture_budget_denied', message: 'Offline fixture exhausted' } } : {}) }
  } else if (packet.payloadKind === 'dsh_budget_settle_request') {
    budgetSettles.push(value)
    kind = 'dsh_budget_settle_result'
    contract = 'genshin-native-dsh-budget-result-v1'
    result = { settled: true, lease_id: value.lease_id }
  } else if (packet.payloadKind === 'dsh_research_register_request') {
    registrations.push(value)
    if (denyResearch) {
      transport.accept({ header: { lane: 'remote_unary', payload_kind: 'dsh_research_register_error',
        correlation_id: packet.correlationId }, payload: Buffer.from(JSON.stringify({
        request_id: value.request_id, session_id: value.session_id, code: 'research_fixture_unavailable',
        message: 'Offline fixture registration failed before dispatch', details: {}, outcome_unknown: false })) })
      return
    }
    kind = 'dsh_research_register_result'
    contract = 'genshin-native-dsh-research-result-v1'
    result = { status: 'registered', research_id: 'research_' + String(registrations.length).padStart(32, 'a'),
      research_ledger_revision: registrations.length,
      arguments_hash: createHash('sha256').update(canonicalEnvelopeJson(value.arguments)).digest('hex'),
      raw_arguments_hash: value.raw_arguments_hash }
  } else if (packet.payloadKind === 'dsh_research_settle_request') {
    settles.push(value)
    kind = 'dsh_research_settle_result'
    contract = 'genshin-native-dsh-research-result-v1'
    result = { settled: true, research_id: value.research_id }
  } else throw new Error('unexpected reverse request ' + packet.payloadKind)
  transport.accept({ header: { lane: 'remote_unary', payload_kind: kind, correlation_id: packet.correlationId },
    payload: Buffer.from(JSON.stringify({ contract, request_id: value.request_id, session_id: value.session_id, result })) })
} })
let host, peer
async function rpc(endpoint, request) {
  const response = await host.handler.fetch(new Request('https://genshin-shell.invalid/api/session/' + endpoint, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: 'session/' + endpoint,
      payload: { args: { request } } }),
  }), peer)
  const result = (await response.json()).result
  assert.equal(result.ok, true, JSON.stringify(result))
  return result.value
}
async function prompt(id) {
  await rpc('prompt', { sessionId: id, requestId: randomUUID(), mode: 'queue',
    content: [{ type: 'text', text: 'Research requested fixture' }] })
  await host.context.agents.get(id).whenIdle().catch(() => {})
}
async function waitFor(predicate) {
  const until = Date.now() + 5_000
  while (!predicate() && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 1))
  assert.ok(predicate(), 'offline fixture condition not reached')
}
const evidence = {}
try {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'),
    domainTransport: transport, accounting: true, providerCapacity: 2,
    providerRetryPolicy: { mode: 'normal', maxRetries: 2,
      backoff: { initialDelayMs: 10, maxDelayMs: 10, jitterRatio: 0 } } })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'accounting-offline-sentinel' })
  await rpc('create', { sessionId: 'accounting-one' })
  assert.equal((await host.sessionStatus('accounting-one')).last_turn, null)
  await assert.rejects(host.sessionStatus('unknown-session'), { code: 'SESSION_QUERY_SESSION_NOT_FOUND' })
  assert.ok(host.context.tools.get('ask_user_question', host.context.agents.get('accounting-one')))
  assert.ok(host.context.tools.get('web_search', host.context.agents.get('accounting-one')))
  await prompt('accounting-one')
  assert.equal(outboundSearches, 1)
  assert.equal(registrations.length, 1)
  assert.equal(settles.length, 1)
  assert.equal(settles[0].status, 'completed')
  assert.equal(leases.length, 2)
  assert.equal(budgetSettles.length, 2)
  for (const lease of leases) {
    assert.equal(lease.session_id, 'accounting-one')
    assert.equal(lease.purpose, 'main')
    assert.equal(lease.turn, 1)
    assert.ok(Number.isInteger(lease.step))
    assert.ok(Number.isSafeInteger(lease.max_tokens) && lease.max_tokens > 0,
      'Official adapter defaults must already be materialized before accounting')
  }
  assert.equal(budgetSettles[1].usage.outputTokens, 7)
  assert.deepEqual((await host.sessionStatus('accounting-one')).last_turn, { turn: 1, reason: { kind: 'completed' } })
  evidence.physicalProviderAttemptsAndUsageBudgeted = true
  const log = await host.context.sessionQuery.readSession('accounting-one')
  const fact = log.events.find(row => row.seq === registrations[0].call_seq)
  assert.equal(fact.type, 'tool/call')
  assert.equal(registrations[0].call_id, String(fact.data.callId))
  evidence.nativeWebRegistersAndSettlesRealDshIdentity = true
  await prompt('accounting-one')
  assert.equal(outboundSearches, 2)
  assert.equal(registrations[1].turn, 2)
  assert.equal(settles.length, 2)
  evidence.secondTurnHasIndependentResearchScope = true
  denyResearch = true
  await prompt('accounting-one')
  assert.equal(outboundSearches, 2)
  denyResearch = false
  // A failed previous scope must not veto a later user Turn forever.
  await prompt('accounting-one')
  assert.ok(leases.at(-1).turn >= 4)
  assert.equal(host.accounts.researchDiagnostics('accounting-one').fatalCode, undefined)
  evidence.failedResearchDoesNotPoisonLaterTurns = true

  mode = 'retry'
  providerRequests = 0
  await rpc('create', { sessionId: 'accounting-retry' })
  const beforeRetryLeases = leases.length
  const beforeRetrySettles = budgetSettles.length
  await prompt('accounting-retry')
  assert.equal(providerRequests, 2)
  assert.equal(leases.length - beforeRetryLeases, 2)
  assert.equal(budgetSettles.length - beforeRetrySettles, 2)
  assert.equal(new Set(leases.slice(beforeRetryLeases).map(row => row.lease_id)).size, 2)
  assert.equal(budgetSettles[beforeRetrySettles].usage, null)
  assert.equal(budgetSettles[beforeRetrySettles + 1].usage.outputTokens, 7)
  assert.equal(leases[beforeRetryLeases].turn, leases[beforeRetryLeases + 1].turn)
  assert.equal(leases[beforeRetryLeases].step, leases[beforeRetryLeases + 1].step)
  evidence.eachPhysicalRetryOwnsDistinctBudgetLease = true

  mode = 'success'
  denyBudget = true
  providerRequests = 0
  const beforeDeniedSettles = budgetSettles.length
  await rpc('create', { sessionId: 'accounting-denied' })
  await prompt('accounting-denied')
  assert.equal(providerRequests, 0)
  assert.equal(budgetSettles.length, beforeDeniedSettles)
  denyBudget = false
  await prompt('accounting-denied')
  assert.equal(providerRequests, 1)
  evidence.budgetDenialPreventsDispatchAndLaterTurnCanProceed = true

  mode = 'cancel'
  providerRequests = 0
  await rpc('create', { sessionId: 'accounting-cancel' })
  await rpc('prompt', { sessionId: 'accounting-cancel', requestId: randomUUID(), mode: 'queue',
    content: [{ type: 'text', text: 'Cancel an admitted physical request' }] })
  await waitFor(() => providerRequests === 1)
  await rpc('cancel', { sessionId: 'accounting-cancel' })
  await host.context.agents.get('accounting-cancel').whenIdle().catch(() => {})
  assert.equal(budgetSettles.at(-1).finish, 'aborted')
  assert.equal(budgetSettles.at(-1).session_id, 'accounting-cancel')
  assert.equal(providerRequests, 1)
  evidence.cancelStillSettlesLeaseWithoutProviderReplay = true

  mode = 'concurrent'
  const beforeConcurrent = leases.length
  for (const id of ['accounting-parallel-one', 'accounting-parallel-two']) {
    await rpc('create', { sessionId: id })
    await rpc('prompt', { sessionId: id, requestId: randomUUID(), mode: 'queue',
      content: [{ type: 'text', text: 'Independent Session accounting' }] })
  }
  await waitFor(() => concurrentRequests === 2)
  assert.equal(host.admission.snapshot().active, 2)
  for (const id of ['accounting-parallel-one', 'accounting-parallel-two']) {
    const status = await host.sessionStatus(id)
    assert.equal(status.agent_running, true)
    assert.equal(status.admission.state, 'active')
    assert.equal(status.admission.active, 1)
    assert.equal(status.admission.waiting, 0)
    assert.equal(status.last_turn, null)
  }
  releaseConcurrent()
  await Promise.all(['accounting-parallel-one', 'accounting-parallel-two']
    .map(id => host.context.agents.get(id).whenIdle()))
  assert.deepEqual(new Set(leases.slice(beforeConcurrent).map(row => row.session_id)),
    new Set(['accounting-parallel-one', 'accounting-parallel-two']))
  assert.equal(new Set(leases.slice(beforeConcurrent).map(row => row.lease_id)).size, 2)
  assert.equal(host.admission.snapshot().active, 0)
  evidence.twoDshHandlesConcurrentLeasesDoNotCrossSessions = true
  evidence.sessionStatusReadsNativeEndAndPhysicalAdmissionOnly = true

  mode = 'success'
  await rpc('create', { sessionId: 'accounting-compaction' })
  await prompt('accounting-compaction') // Establish the public durable routed target.
  const compacting = host.context.agents.get('accounting-compaction')
  // Exercise the official default threshold, not a smaller production policy.
  // Several replaceable old nodes leave a recent tail for the DSH compressor.
  for (let index = 0; index < 16; index += 1) {
    compacting.session.append('user/message', createUserMessage({
      content: [{ type: 'text', text: `OLD_HISTORY_${index}_${'x'.repeat(600_000)}` }],
      source: { kind: 'user' },
    }), { surfaceOp: 'append' })
  }
  assert.ok(host.context.tokenMeter.measure(compacting.session).totalTokens > 800_000)
  const beforeCompaction = leases.length
  await prompt('accounting-compaction')
  const auxiliary = leases.slice(beforeCompaction).find(row => row.purpose === 'compaction')
  assert.ok(auxiliary, JSON.stringify(leases.slice(beforeCompaction)))
  assert.equal(auxiliary.session_id, 'accounting-compaction')
  assert.ok(auxiliary.turn === null || Number.isSafeInteger(auxiliary.turn))
  assert.ok(auxiliary.step === null || Number.isSafeInteger(auxiliary.step))
  assert.ok(Number.isSafeInteger(auxiliary.max_tokens) && auxiliary.max_tokens > 0)
  evidence.resolvedDefaultMaxTokens = { main: leases[0].max_tokens, compaction: auxiliary.max_tokens }
  const auxiliaryUsage = budgetSettles.find(row => row.lease_id === auxiliary.lease_id)?.usage
  assert.ok(auxiliaryUsage?.inputTokens >= 1_000, JSON.stringify(auxiliaryUsage))
  assert.equal(auxiliaryUsage.cacheReadTokens, 809_000)
  assert.ok((await host.context.sessionQuery.readSession('accounting-compaction')).events
    .some(row => row.type === 'compaction/end' && row.data.error === undefined))
  evidence.officialCompactionUsesSameSessionBudgetAndNativeUsage = true
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-accounting-probe-v1', status: 'passed', ...evidence }))
} finally {
  transport.close()
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
