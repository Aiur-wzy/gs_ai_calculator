/** Public DSH compiler, real first provider request, and a Python App Host. */
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createInterface } from 'node:readline'
import { pathToFileURL } from 'node:url'
import { compileNativeAppToolDefinitions, validateNativeAppToolArguments,
  nativeAppToolWireSchemas } from '../agent-runtime/src/native-app-tool-schema.mjs'
import { createNativeDshSessionHost } from '../agent-runtime/src/native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from '../agent-runtime/src/native-dsh-domain-fixture-fetch.mjs'

const requireRuntime = createRequire(new URL('../agent-runtime/package.json', import.meta.url))
const dshTools = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-tools')).href)
const input = createInterface({ input: process.stdin, crlfDelay: Infinity })[Symbol.asyncIterator]()
async function receive() {
  const item = await input.next()
  if (item.done) throw new Error('python_peer_closed')
  return JSON.parse(item.value)
}
function send(value) { process.stdout.write(`SCHEMA_FEEDBACK ${JSON.stringify(value)}\n`) }
const config = await receive()
for (const tool of config.snapshot.tools) {
  try { compileNativeAppToolDefinitions([tool], async () => ({})) }
  catch (error) { throw new Error(`uncompilable_actual_tool:${tool.name}:${error.message}`) }
}

// Exercise every supported bound and preserve nested/json/nullable/union shape.
const fixture = { name: 'gs_read_context', description: 'offline bound matrix', inputSchema: {
  type: 'object', additionalProperties: false, properties: {
    list: { type: 'array', minItems: 0, maxItems: 2, description: 'keep list meaning',
      items: { type: 'string', minLength: 1, maxLength: 2 } },
    text: { type: 'string', minLength: 0, maxLength: 1, description: 'keep text meaning' },
    amount: { type: 'number', minimum: 0, maximum: 2, exclusiveMinimum: -1, exclusiveMaximum: 3 },
    nullable: { anyOf: [{ type: 'array', minItems: 1, items: { type: 'integer' } }, { type: 'null' }] },
    choice: { oneOf: [{ type: 'string', enum: ['known'], minLength: 1 },
      { type: 'integer', const: 2, maximum: 2 }] },
    extra: {}, typedJson: { type: 'json', description: 'arbitrary finite JSON' },
    nested: { type: 'object', additionalProperties: false, properties: {
      exact: { type: 'array', minItems: 1, maxItems: 1, items: { type: 'string' } },
    } },
  },
} }
let matrixInvocations = 0
const [matrixTool] = compileNativeAppToolDefinitions([fixture], async () => { matrixInvocations++; return {} })
const matrixSchema = nativeAppToolWireSchemas([matrixTool])[0].parameters
dshTools.assertSupportedJsonSchema(matrixSchema)
assert.match(matrixSchema.properties.list.description, /^keep list meaning\n/)
assert.match(matrixSchema.properties.list.description, /minItems=0.*maxItems=2/)
assert.match(matrixSchema.properties.list.items.description, /minLength=1.*maxLength=2/)
assert.match(matrixSchema.properties.text.description, /minLength=0.*maxLength=1/)
for (const key of ['minimum=0', 'maximum=2', 'exclusiveMinimum=-1', 'exclusiveMaximum=3']) {
  assert.ok(matrixSchema.properties.amount.description.includes(key))
}
assert.match(matrixSchema.properties.nullable.oneOf[0].description, /minItems=1/)
assert.equal(matrixSchema.properties.nullable.oneOf[1].type, 'null')
assert.deepEqual(matrixSchema.properties.extra, {})
assert.deepEqual(matrixSchema.properties.typedJson, { description: 'arbitrary finite JSON' })
assert.equal(matrixSchema.additionalProperties, undefined) // official closed parameter root
assert.equal(matrixSchema.properties.nested.additionalProperties, false)
await matrixTool.execute({ list: [], text: '🧊', amount: 0, nullable: null, choice: 'known',
  extra: { untouched: [true, null] }, typedJson: [1, '冰'], nested: { exact: ['one'] } }, {})
let rejected = 0
for (const [args, path, constraint, expected] of [
  [{ list: ['one', 'two', 'three'] }, 'arguments.list', 'maxItems', 2],
  [{ list: [''] }, 'arguments.list[0]', 'minLength', 1],
  [{ list: ['abc'] }, 'arguments.list[0]', 'maxLength', 2],
  [{ text: '冰水' }, 'arguments.text', 'maxLength', 1],
  [{ amount: -1 }, 'arguments.amount', 'minimum', 0],
  [{ amount: 3 }, 'arguments.amount', 'maximum', 2],
  [{ nested: { exact: [] } }, 'arguments.nested.exact', 'minItems', 1],
  [{ nullable: [] }, 'arguments.nullable', 'anyOf', 'at_least_one_matching_branch'],
]) {
  await assert.rejects(matrixTool.execute(args, {}), error => {
    const payload = JSON.parse(error.message)
    assert.equal(payload.path, path)
    assert.equal(payload.constraint, constraint)
    assert.deepEqual(payload.expected, expected)
    assert.equal(payload.execution_state, 'did_not_execute')
    assert.equal(payload.app_call_admission, 'not_recorded')
    assert.equal(payload.operation_may_have_completed, false)
    assert.equal(payload.contract_ref.pointer, '/tools/gs_read_context')
    assert.equal(payload.recovery.action, 'correct_arguments')
    assert.ok(!('system_fault' in payload) && !('failure_kind' in payload))
    rejected++
    return true
  })
}
// DSH can reject structural/lossless input before the App callback. Preserve
// its pipeline; test the precise App boundary separately for the same values.
for (const [args, path, constraint, expected] of [
  [{ choice: 'unknown' }, 'arguments.choice', 'oneOf', 'exactly_one_matching_branch'],
  [{ nested: { extra: true } }, 'arguments.nested.extra', 'additionalProperties', false],
  [{ extra: { value: Number.NaN } }, 'arguments.extra.value', 'portable_json', 'finite_lossless_json'],
]) {
  assert.throws(() => validateNativeAppToolArguments(fixture, args), error => {
    const payload = JSON.parse(error.message)
    return payload.path === path && payload.constraint === constraint
      && JSON.stringify(payload.expected) === JSON.stringify(expected)
  })
  await assert.rejects(matrixTool.execute(args, {}))
  rejected++
}
for (const [keyword, value, invalid, type] of [
  ['minimum', 0, -1, 'number'], ['maximum', 0, 1, 'number'],
  ['exclusiveMinimum', 0, 0, 'number'], ['exclusiveMaximum', 0, 0, 'number'],
  ['minItems', 1, [], 'array'], ['maxItems', 0, ['x'], 'array'],
  ['minLength', 1, '', 'string'], ['maxLength', 0, 'x', 'string'],
]) {
  const definition = { ...fixture, inputSchema: { type: 'object', additionalProperties: false,
    properties: { value: { type, [keyword]: value,
      ...(type === 'array' ? { items: { type: 'string' } } : {}) } } } }
  const [tool] = compileNativeAppToolDefinitions([definition], async () => { throw new Error('must not invoke') })
  assert.ok(tool.parameters.properties.value.description.includes(`${keyword}=${value}`))
  assert.throws(() => validateNativeAppToolArguments(definition, { value: invalid }), error => {
    const detail = JSON.parse(error.message)
    return detail.path === 'arguments.value' && detail.constraint === keyword && detail.expected === value
  })
  rejected++
}
assert.equal(matrixInvocations, 1)

const requests = [], invocations = []
const originalFetch = globalThis.fetch
const fakeFetch = createNativeDshDomainFixtureFetch({ repeatCalls: false,
  calls: [{ tool: 'gs_record_finding', arguments: config.invalid },
          { tool: 'gs_record_finding', arguments: config.corrected }],
  onRequest(value) { requests.push(value) },
})
globalThis.fetch = async (url, init) => {
  // The existing fixture also serves the adapter's public /models catalog;
  // it never delegates to the real fetch implementation.
  return fakeFetch(url, init)
}
const transport = {
  async bindSession(sessionId) {
    return { session_id: sessionId, binding_state: 'bound', snapshot: config.snapshot,
      runtime_context: { workflow: 'artifact' },
      prompt_bundle: { system_prompt: 'Offline fixed schema-feedback engineering case.', required_skill_resources: [] } }
  },
  async execute(body) {
    invocations.push(body)
    send({ type: 'invoke', body })
    return receive()
  },
}
let host, peer
try {
  host = await createNativeDshSessionHost({ profileRoot: config.profile_root, domainTransport: transport })
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1,
    secret: 'offline-schema-feedback-engineering-secret' })
  peer = host.createPeer()
  async function rpc(method, request) {
    const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${method}`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ type: 'client-request', rpcId: `${method}-schema-feedback`,
        method: `session/${method}`, payload: { args: { request } } }),
    }), peer)
    const result = (await response.json()).result
    assert.equal(result.ok, true)
    return result.value
  }
  await rpc('create', { sessionId: config.session_id })
  await rpc('prompt', { sessionId: config.session_id, requestId: 'schema-feedback-prompt',
    mode: 'queue', content: [{ type: 'text', text: 'Run fixed malformed note, correct it, then finish.' }] })
  await host.context.agents.get(config.session_id).whenIdle()
  send({ type: 'stage', name: 'agent_idle' })
  const observed = await host.context.sessionQuery.readSession(config.session_id)
  send({ type: 'stage', name: 'read_session' })
  const recordTool = requests[0].tools.find(x => x.name === 'gs_record_finding')
  assert.ok(recordTool, JSON.stringify({ tools: requests[0].tools.map(x => x.name),
    runtime: requests[0].body.messages.filter(x => JSON.stringify(x).includes('binding_issue')) }))
  const first = recordTool.inputSchema
  assert.match(first.properties.record.properties.adopted_refs.description, /minItems=1/)
  assert.match(first.properties.record.properties.adopted_refs.description, /maxItems=128/)
  dshTools.assertSupportedJsonSchema(first)
  const results = observed.events.filter(x => x.type === 'tool/result').map(x => x.data.message)
  assert.equal(results.length, 2)
  assert.equal(results[0].isError, true)
  const feedback = JSON.parse(results[0].content[0].text.replace(/^Error: /, ''))
  assert.equal(feedback.path, 'arguments.record.adopted_refs')
  assert.equal(feedback.constraint, 'minItems')
  assert.equal(feedback.expected, 1)
  assert.equal(feedback.app_call_admission, 'not_recorded')
  assert.equal(feedback.execution_state, 'did_not_execute')
  assert.equal(feedback.operation_may_have_completed, false)
  assert.deepEqual(feedback.contract_ref, { resource: 'tool_contracts', pointer: '/tools/gs_record_finding' })
  assert.equal(results[1].isError, false)
  assert.equal(invocations.length, 1)
  assert.deepEqual(JSON.parse(invocations[0].arguments_json), config.corrected)
  send({ type: 'done', actual_first_request_schema: first, malformed_prehost_feedback: feedback,
    invalid_is_error: results[0].isError, corrected_is_error: results[1].isError,
    source_host_invocations: invocations.length, requests: requests.length,
    generic_bound_counterexamples: rejected, no_network: true })
} finally {
  send({ type: 'stage', name: 'disposing_peer' })
  await peer?.dispose()
  send({ type: 'stage', name: 'disposing_host' })
  await host?.dispose()
  send({ type: 'stage', name: 'disposed' })
  globalThis.fetch = originalFetch
  process.stdin.destroy()
}
