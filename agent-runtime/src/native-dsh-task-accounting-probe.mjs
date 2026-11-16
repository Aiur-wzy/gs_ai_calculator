/** Real official DSH spawn/loop with an offline App accounting/domain peer. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'
import { hashAppToolsSnapshotBody, APP_TOOLS_SNAPSHOT_CONTRACT, MAX_SEQUENTIAL_APP_CALLS } from './native-app-tool-catalog.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { drainNativeJobs } from './native-dsh-job-drain.mjs'
import { NativeTaskTransport } from './native-dsh-task-transport.mjs'

assertPinnedNodeVersion()
const tempBase = resolve('.codex-tmp')
await mkdir(tempBase, { recursive: true })
const root = await mkdtemp(join(tempBase, 'task-accounting-'))
const owner = 'task-owner-root'
const marker = 'NATIVE_CHILD_ACCOUNTING_ONLY'
const parentFetch = createNativeDshDomainFixtureFetch({ repeatCalls: false, calls: [
  { tool: 'todo_write', arguments: { todos: [{ content: 'Offline accounting test', status: 'in_progress' }] } },
  { tool: 'todo_write', arguments: { todos: [{ content: 'Offline accounting test', status: 'completed' }] } },
  { tool: 'subagent', arguments: { description: 'Inspect current candidate', prompt: marker } },
] })
const childFetch = createNativeDshDomainFixtureFetch({ repeatCalls: false, calls: [
  { tool: 'gs_read_context', arguments: { resource: 'candidate' } },
] })
const originalFetch = globalThis.fetch
globalThis.fetch = async (url, init) => {
  if (String(url).endsWith('/v1/messages')) {
    const body = JSON.parse(init.body)
    const user = body.messages?.find(row => row.role === 'user')
    const response = await (JSON.stringify(user?.content ?? '').includes(marker) ? childFetch(url, init) : parentFetch(url, init))
    // Provider-issued IDs are correlation values, not globally unique event
    // identities. Reuse one across real model steps and verify exact charging.
    return new Response((await response.text()).replaceAll(/scoped-app-call-\d+-\d+/gu, 'provider-reused-call-id'),
      { status: response.status, headers: response.headers })
  }
  return parentFetch(url, init)
}
const policies = {
  gs_read_context: { policy: 'domain_read', effect: 'none' },
  gs_app_get_workspace: { policy: 'app_workspace_read', effect: 'external_action_ledger' },
  gs_app_request_action: { policy: 'app_action_request', effect: 'external_action_ledger' },
  gs_list_research_calls: { policy: 'research_ledger_read', effect: 'external_research_ledger' },
  gs_report_research_usage: { policy: 'research_ledger_write', effect: 'external_research_ledger' },
}
function snapshot() {
  const body = { contract: APP_TOOLS_SNAPSHOT_CONTRACT, workflow: 'artifact', workflow_stage: 'loadout_draft',
    document_id: 'one-root-document', candidate_revision: 0, stage_revision: 0,
    database_fingerprint: 'a'.repeat(64), tool_manifest_hash: 'b'.repeat(64), runtime_generation: 'dsh_v2',
    writer_fence: 'one-root-writer', max_app_calls: MAX_SEQUENTIAL_APP_CALLS,
    tools: Object.keys(policies).sort().map(name => ({ type: 'function', name, description: 'Offline engineering ' + name,
      inputSchema: { type: 'object', properties: name === 'gs_read_context' ? { resource: { type: 'string' } } : {},
        additionalProperties: false } })), policies }
  return { ...body, catalog_hash: hashAppToolsSnapshotBody(body) }
}
const binds = [], calls = [], provider = [], settled = [], generic = []
const transport = {
  async bindSession(sessionId) {
    binds.push(sessionId)
    return { session_id: sessionId, binding_state: 'bound', snapshot: snapshot(), runtime_context: {},
      prompt_bundle: { system_prompt: 'Only use exact published tools; this is an offline engineering fixture.', required_skill_resources: [] } }
  },
  async execute(body) {
    calls.push(body)
    assert.equal(body.raw_arguments_hash, createHash('sha256').update(body.arguments_json).digest('hex'))
    return { session_id: body.session_id, call_id: body.call_id, call_seq: body.call_seq, status: 'settled_success',
      result: { code: 0, data: { candidate_revision: 0, source: 'root-candidate' } }, next_snapshot: snapshot() }
  },
  async budgetAcquire(body) { provider.push(body); return { result: { allowed: true, lease_id: body.lease_id } } },
  async budgetSettle(body) { settled.push(body); return { result: { status: 'settled', lease_id: body.lease_id } } },
  async admitTool(body) { generic.push(body); return { result: { status: 'reserved' } } },
  async researchRegister() { throw new Error('probe does not perform network research') },
  async researchSettle() { throw new Error('probe does not perform network research') },
}
let host, peer
try {
  host = await createNativeDshSessionHost({ profileRoot: root, domainTransport: transport, accounting: true, providerCapacity: 2 })
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'offline-task-probe-sentinel' })
  peer = host.createPeer()
  const rpc = async (method, request) => {
    const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${method}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ type: 'client-request',
        rpcId: randomUUID(), method: `session/${method}`, payload: { args: { request } } }),
    }), peer)
    const result = (await response.json()).result
    assert.equal(result.ok, true, JSON.stringify(result))
    return result.value
  }
  await rpc('create', { sessionId: owner })
  await rpc('prompt', { sessionId: owner, requestId: randomUUID(), mode: 'queue', content: [{ type: 'text', text: 'NATIVE_ROOT_TASK' }] })
  const parent = host.context.agents.get(owner)
  await parent.whenIdle()
  while (await host.context.subagents.waitForTreeWork(parent)) await parent.whenIdle()
  const parentLog = await host.context.sessionQuery.readSession(owner)
  const children = await host.context.subagents.listDescendants(owner)
  assert.equal(children.length, 1, JSON.stringify(parentLog.events))
  const child = await host.context.sessionQuery.readSession(children[0].id)
  const observedChild = await host.context.sessionQuery.observeSession(children[0].id, { projectionMode: 'none' })
  try {
    assert.equal(observedChild.header.parentSession, owner)
    assert.equal(observedChild.header.origin, 'subagent')
  } finally { observedChild[Symbol.dispose]() }
  assert.ok(binds.length >= 2 && binds.every(id => id === owner), JSON.stringify(binds))
  assert.equal(calls.length, 1, JSON.stringify(calls))
  assert.equal(calls[0].session_id, owner)
  assert.match(calls[0].call_id, /^child-[a-f0-9]{64}$/u)
  assert.equal(calls[0].writer_fence, 'one-root-writer')
  assert.ok(generic.length > 0 && generic.every(row => row.session_id === owner))
  assert.equal(generic.length, 3)
  assert.equal(new Set(generic.map(row => row.claim_id)).size, generic.length,
    'Distinct official DSH tool steps must not share a budget reservation')
  assert.ok(provider.length >= 4 && provider.every(row => row.session_id === owner), JSON.stringify(provider))
  assert.equal(settled.length, provider.length)
  for (const grant of provider) {
    const settlement = settled.find(row => row.lease_id === grant.lease_id)
    for (const key of ['session_id', 'turn', 'step', 'purpose']) assert.equal(settlement[key], grant[key])
  }
  assert.ok(child.events.some(row => row.type === 'plugin:genshin/task-attribution'))
  assert.ok(child.events.some(row => row.type === 'tool/call' && row.data.name === 'gs_read_context'))
  assert.ok(child.events.some(row => row.type === 'tool/result' && row.data.message.isError === false))
  // The child is already cold-disposed by upstream. Its actual persisted
  // header still resolves to the same App owner without recreating a child.
  const rebound = await new NativeTaskTransport(host.context, transport).bindSession(children[0].id)
  assert.equal(rebound.session_id, children[0].id)
  assert.equal(binds.at(-1), owner)
  let cancelRequested = false, finishJob
  const done = new Promise(resolve => { finishJob = resolve })
  const jobId = host.context.jobs.start({ kind: 'shell', label: 'offline owned shutdown fixture', owner: parent.id,
    run() { return { done, cancel() { cancelRequested = true; setTimeout(() => finishJob({ status: 'killed' }), 5) } } } })
  const jobs = await drainNativeJobs(host.context, host.context.agents.list())
  assert.equal(cancelRequested, true)
  assert.equal(host.context.jobs.get(jobId, owner).status, 'killed')
  assert.ok(jobs.terminal >= 1)
  console.log(JSON.stringify({ contract: 'native-task-accounting-probe-v1', status: 'passed',
    actualDshChild: children[0].id, parent: owner, binds: binds.length, domainCalls: calls.length,
    genericClaims: generic.length, providerRequests: provider.length, settledRequests: settled.length,
    repeatedProviderIdsCountedPerStep: true,
    childDescriptorSeen: child.events.some(row => row.type === 'subagent/descriptor'),
    coldChildRebound: true, ownedJobCancelledAndSettled: true }))
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  assert.ok(root.startsWith(join(tempBase, 'task-accounting-')))
  await rm(root, { recursive: true, force: true })
}
