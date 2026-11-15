/** Real public DSH services; provider responses are local fixtures, no API. */
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { join, resolve, sep } from 'node:path'
import { randomUUID } from 'node:crypto'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'
import { enableCliPtc, startReadonlyReview } from './dsh-task-review-experiments.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { setSandboxMode } from '@deepseek-ai/dsh-sandbox-policy'

assertPinnedNodeVersion()
const tempBase = resolve('.codex-tmp')
const root = await mkdtemp(join(tempBase, 'task-review-integration-'))
assert.ok(root.startsWith(tempBase + sep))
await mkdir(join(root, 'media'))
const previousFetch = globalThis.fetch
let host, peer
async function rpc(endpoint, request) {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: `session/${endpoint}`,
      payload: { args: { request } } }),
  }), peer)
  const envelope = await response.json()
  assert.equal(envelope.result.ok, true, JSON.stringify(envelope.result))
  return envelope.result
}
try {
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [] })
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media') })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'offline-probe' })
  await rpc('create', { sessionId: 'cli-experiment' })
  const agent = host.context.agents.get('cli-experiment')
  const context = host.context
  const scope = context.tools.schemas(agent)
  const reader = scope.find(tool => tool.name === 'read')
  assert.ok(reader, 'official file tools must exist')
  const cwd = await context.workingDirectory.ensure(agent)
  await writeFile(join(cwd, 'probe.txt'), 'local task evidence')
  // This probe only reads; do not request OS write grants for the PTC worker.
  setSandboxMode(agent.session, 'read-only')
  const disposePtc = enableCliPtc(agent, { enabled: true })
  const assembly = await context.systemPrompt.assemble({ scope: agent, agent })
  assert.deepEqual(assembly.tools.map(tool => tool.name), ['run_code'])
  // Uses the official generated SDK and registry, not a bespoke batch tool.
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'run_code', arguments: {
    code: 'return await tools.read({file_path:"probe.txt"})', description: '读取本地测试资料',
  } }], repeatCalls: false })
  await rpc('prompt', { sessionId: agent.id, requestId: randomUUID(), mode: 'queue', content: [{ type: 'text', text: 'Read the local fixture.' }] })
  await agent.whenIdle()
  const log = await context.sessionQuery.readSession(agent.id)
  const dispatch = log.events.filter(row => row.type === 'tool/ptc-dispatch')
  assert.ok(dispatch.some(row => row.data.name === 'read'), JSON.stringify(log.events.filter(row => row.type.startsWith('tool/'))))
  assert.ok(log.events.some(row => row.type === 'tool/result' && JSON.stringify(row.data).includes('local task evidence')))
  disposePtc()
  assert.ok(context.tools.schemas(agent).some(tool => tool.name === 'read'))
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [] })
  const originalCount = globalThis.fetch.requestCount
  await startReadonlyReview(context, { parent: agent, enabled: false })
  assert.equal(globalThis.fetch.requestCount, originalCount)
  const activation = await startReadonlyReview(context, { enabled: true, parent: agent,
    signal: new AbortController().signal, maxTokens: 1024, evidence: '本地夹具：第一轮输入了扣能；第二轮回能尚未提供。' })
  const reviewed = await activation.result
  assert.equal(reviewed.stopReason, 'completed')
  const child = context.agents.get(activation.childId)
  if (child) {
    const denied = await child.ctx.tools.execute({ callId: 'readonly-denial', name: 'read',
      arguments: { file_path: 'probe.txt' }, agent: child, signal: new AbortController().signal })
    assert.equal(denied.isError, true)
  }
  await agent.whenIdle()
  console.log(JSON.stringify({ status: 'passed', dsh: '0.2.1-rc.5', provider: 'offline-fixture',
    cliPtcScope: 'actual-file-tool-dispatch', readonlyReview: 'official-spawn-explicit-budget',
    nativeDomainPtc: 'unsupported-call-identity' }))
} finally {
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = previousFetch
  await rm(root, { recursive: true, force: true })
}
