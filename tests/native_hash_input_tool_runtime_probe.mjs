/** Real pinned DSH ToolRuntime; the Python peer owns the actual App ledger. */
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createInterface } from 'node:readline'
import { pathToFileURL } from 'node:url'
import { compileNativeAppToolDefinitions } from '../agent-runtime/src/native-app-tool-schema.mjs'
import { createNativeDeepSeekKernel } from '../agent-runtime/src/native-deepseek-kernel.mjs'
import { createProductionCompositionProbeFixture } from '../agent-runtime/src/production-composition-probe-fixture.mjs'

const requireRuntime = createRequire(new URL('../agent-runtime/package.json', import.meta.url))
const { SessionId } = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-session')).href)

const input = createInterface({ input: process.stdin, crlfDelay: Infinity })[Symbol.asyncIterator]()
async function receive() {
  const item = await input.next()
  if (item.done) throw new Error('python_peer_closed')
  return JSON.parse(item.value)
}
function send(value) { process.stdout.write(`HASH_RUNTIME ${JSON.stringify(value)}\n`) }
const config = await receive()
const originalFetch = globalThis.fetch
globalThis.fetch = async () => { throw new Error('offline_hash_runtime_network_forbidden') }
let profile, kernel, handle, calls = 0
try {
  profile = await createProductionCompositionProbeFixture('hash-input-sdk')
  kernel = await createNativeDeepSeekKernel({ ...profile.paths })
  const [definition] = compileNativeAppToolDefinitions([config.definition], async (_row, args) => {
    calls += 1
    send({ type: 'invoke', args, index: calls })
    const reply = await receive()
    assert.equal(reply.terminal_state, 'settled_success')
    return reply.result
  })
  handle = await kernel.context.agents.create({
    sessionId: SessionId('hash-runtime-offline'),
    agentOptions: { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high' },
    async setup(scope) { scope.tools.register(definition) },
  })
  for (const [index, args] of config.cases.entries()) {
    const batch = await kernel.context.tools.execute({
      calls: [{ type: 'tool-call', id: `hash-valid-${index}`, name: config.definition.name,
        arguments: JSON.stringify(args) }],
      agent: handle.agent, signal: new AbortController().signal,
    })
    send({ type: 'case', index, result: batch.results[0].result })
  }
  const beforeInvalid = calls
  for (const [index, args] of [
    '{"kind":"unsupported","value":{}}',
    '{"kind":"canonical_json","value":NaN}',
    '{"kind":"canonical_json","value":{"nested":Infinity}}',
    '{"kind":"canonical_json","value":{"nested":undefined}}',
    '{"kind":"canonical_json","value":-0}',
    '{"kind":"canonical_json","value":9007199254740992}',
  ].entries()) {
    const batch = await kernel.context.tools.execute({
      calls: [{ type: 'tool-call', id: `hash-invalid-${index}`, name: config.definition.name, arguments: args }],
      agent: handle.agent, signal: new AbortController().signal,
    })
    assert.equal(batch.results[0].result.isError, true)
  }
  assert.equal(calls, beforeInvalid)
  send({ type: 'done', invoked: calls, invalid_rejected_before_host: 6, network_calls: 0 })
} finally {
  await handle?.dispose()
  await kernel?.dispose()
  await profile?.cleanup()
  globalThis.fetch = originalFetch
  process.stdin.destroy()
}
