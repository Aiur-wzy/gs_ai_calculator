/** Real DSH schema compiler and pure App catalog; no legacy dispatcher. */
import assert from 'node:assert/strict'
import { compileNativeAppToolDefinitions, validateNativeAppToolSchema,
  validateNativeAppToolArguments } from './native-app-tool-schema.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const schema = { type: 'object', additionalProperties: false, required: ['amount'], properties: {
  amount: { type: 'number', minimum: 0, maximum: 1 },
  options: { type: 'object', additionalProperties: false, required: ['tags'], properties: {
    tags: { type: 'array', items: { type: 'string' }, maxItems: 4 },
    mode: { enum: ['short', 'full'] },
    selector: { oneOf: [{ type: 'integer' }, { type: 'null' }] },
    extra: {},
  } },
} }
const definition = { name: 'gs_read_context', description: 'Pure nested schema fixture', inputSchema: schema }
validateNativeAppToolSchema(definition)
let received
const [tool] = compileNativeAppToolDefinitions([definition], async (row, args, exec) => {
  received = { row, args, callId: exec.callId }
  return { code: 0, value: args.amount }
})
assert.equal(tool.parameters.properties.amount.type, 'number')
assert.equal(tool.parameters.properties.options.properties.tags.type, 'array')
assert.equal(tool.parameters.properties.options.properties.selector.oneOf.length, 2)
assert.deepEqual(tool.parameters.properties.options.properties.mode.enum, ['short', 'full'])
const args = { amount: 0.125, options: { tags: ['冰'], selector: null, extra: { nested: true } } }
validateNativeAppToolArguments(definition, args)
assert.deepEqual(await tool.execute(args, { callId: 'actual-dsh-call' }), { code: 0, value: 0.125 })
assert.equal(received.callId, 'actual-dsh-call')
assert.equal(received.row.name, definition.name)
assert.equal(received.args, args)
for (const invalid of [
  { type: 'object', properties: { nested: { type: 'object', properties: {}, required: ['missing'] } }, additionalProperties: false },
  { type: 'object', properties: { text: { type: 'string', maxLength: '4' } }, additionalProperties: false },
  { type: 'object', properties: {}, additionalProperties: true },
  { type: 'object', properties: { value: { anyOf: [{ type: 'string' }, { type: 'string' }] } }, additionalProperties: false },
  { type: 'object', properties: { value: { anyOf: [{ type: 'integer' }, { type: 'number' }] } }, additionalProperties: false },
  { type: 'object', properties: { value: { anyOf: [{ type: 'json' }, { type: 'string' }] } }, additionalProperties: false },
  { type: 'object', properties: { value: { $ref: '#/unsupported' } }, additionalProperties: false },
]) assert.throws(() => validateNativeAppToolSchema({ ...definition, inputSchema: invalid }),
  { code: 'snapshot_schema_unsupported' })
for (const invalid of [
  {}, { amount: 2 }, { amount: 0.5, unknown: true },
  { amount: 0.5, options: { tags: [], selector: 'not-an-integer' } },
  { amount: 0.5, options: { tags: ['a', 'b', 'c', 'd', 'e'] } },
]) assert.throws(() => validateNativeAppToolArguments(definition, invalid))
await assert.rejects(tool.execute({ amount: 2 }, { callId: 'invalid-call' }))
assert.equal(received.callId, 'actual-dsh-call')
const unsafe = { type: 'object', additionalProperties: false, properties: { value: {} } }
const unsafeDefinition = { ...definition, inputSchema: unsafe }
for (const value of [Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53, -0]) {
  assert.throws(() => validateNativeAppToolArguments(unsafeDefinition, { value }))
}
console.log(JSON.stringify({ status: 'passed', publicDshCompiler: true,
  nestedNullableNumericConstraints: true, lossySchemasRejected: true,
  invalidArgumentsRejected: true, noLegacyDispatcher: true }))
