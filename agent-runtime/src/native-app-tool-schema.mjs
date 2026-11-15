/** Pure App JSON Schema projection; no transport, dispatcher or Agent owner. */
import { defineTool, parameterSchemaSpecToJsonSchema } from '@deepseek-ai/dsh-tools'
const JSON_SAFE_INTEGER_MAX = 2 ** 53 - 1
const SCHEMA_KEYS = new Set([
  'type', 'properties', 'required', 'additionalProperties', 'items',
  'enum', 'const', 'oneOf', 'anyOf', 'description', 'title', 'default',
  'examples', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
  'minLength', 'maxLength', 'minItems', 'maxItems',
])
const ANNOTATION_KEYS = new Set(['description', 'title', 'default', 'examples'])
const BOUND_KEYS = [
  'minItems', 'maxItems', 'minLength', 'maxLength',
  'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
]
const SUPPORTED_SCHEMA_TYPES = new Set([
  'string', 'integer', 'number', 'boolean', 'null', 'array', 'object', 'json',
])

export class NativeAppToolSchemaError extends Error {
  constructor(code, message = code, details = undefined) {
    super(message)
    this.name = 'NativeAppToolSchemaError'
    this.code = code
    this.details = details
  }
}

function reject(code, message = code, details = undefined) {
  throw new NativeAppToolSchemaError(code, message, details)
}

function plainRecord(value, code, path) {
  const invalid = () => reject(code, path === undefined ? code : `${path} must be a plain object`,
    path === undefined ? undefined : { path, constraint: 'type', expected: 'plain_object' })
  if (value === null || typeof value !== 'object' || Array.isArray(value)) invalid()
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) invalid()
  return value
}

function argumentFailure(path, constraint, expected, message) {
  reject('arguments_schema', message ?? `${path} violates ${constraint}=${JSON.stringify(expected)}`,
    { path, constraint, expected: structuredClone(expected) })
}

function portableFailure(path, message) {
  reject('arguments_json', message, { path, constraint: 'portable_json', expected: 'finite_lossless_json' })
}

function assertSchemaObject(schema, path) {
  plainRecord(schema, 'snapshot_schema_unsupported')
  for (const key of Object.keys(schema)) {
    if (!SCHEMA_KEYS.has(key)) reject('snapshot_schema_unsupported', `Unsupported key ${path}.${key}`)
  }
  return schema
}

function jsonScalar(value) {
  return value === null || typeof value === 'string' || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isFinite(value)
      && !Object.is(value, -0)
      && (!Number.isInteger(value) || Number.isSafeInteger(value)))
}

function validatePortableJson(value, path = 'value', depth = 0, seen = new Set()) {
  if (depth > 64) portableFailure(path, `${path} exceeds maximum depth`)
  if (jsonScalar(value)) return
  if (Array.isArray(value)) {
    if (seen.has(value)) portableFailure(path, `${path} is cyclic`)
    seen.add(value)
    for (let index = 0; index < value.length; index += 1) {
      validatePortableJson(value[index], `${path}[${index}]`, depth + 1, seen)
    }
    seen.delete(value)
    return
  }
  if (value === null || typeof value !== 'object') {
    portableFailure(path, `${path} must be finite lossless JSON`)
  }
  plainRecord(value, 'arguments_json', path)
  if (seen.has(value)) portableFailure(path, `${path} is cyclic`)
  seen.add(value)
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== 'string') portableFailure(path, `${path} has a symbol key`)
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (descriptor === undefined || !Object.hasOwn(descriptor, 'value')) {
      portableFailure(`${path}.${key}`, `${path}.${key} is not a data property`)
    }
    validatePortableJson(descriptor.value, `${path}.${key}`, depth + 1, seen)
  }
  seen.delete(value)
}

function canonicalDataJson(value) {
  validatePortableJson(value, 'arguments')
  function normalize(item) {
    if (Array.isArray(item)) return item.map(normalize)
    if (item !== null && typeof item === 'object') {
      // A normal object would interpret assignment to `__proto__` as a
      // prototype mutation.  Build a null-prototype record using explicit
      // data properties so every admitted JSON key participates in the
      // canonical comparison.
      const result = Object.create(null)
      for (const key of Object.keys(item).sort()) {
        Object.defineProperty(result, key, {
          value: normalize(item[key]),
          enumerable: true,
          configurable: false,
          writable: false,
        })
      }
      return result
    }
    return item
  }
  return JSON.stringify(normalize(value))
}

function parseJsonObject(raw, code) {
  if (typeof raw !== 'string' || raw.length === 0) reject(code)
  let value
  try {
    value = JSON.parse(raw)
  } catch {
    reject(code)
  }
  plainRecord(value, code)
  validatePortableJson(value)
  return value
}

function enumMatches(value, choices) {
  return choices.some(choice => Object.is(choice, value))
}

function branchMatches(value, schema, path, depth) {
  try {
    validateSchemaValue(value, schema, path, depth)
    return true
  } catch (error) {
    if (!(error instanceof NativeAppToolSchemaError)) throw error
    return false
  }
}

function validateSchemaValue(value, schema, path = 'arguments', depth = 0) {
  if (depth > 64) reject('snapshot_schema_unsupported', `Schema too deep at ${path}`)
  assertSchemaObject(schema, path)
  if (Array.isArray(schema.oneOf) || Array.isArray(schema.anyOf)) {
    const branches = schema.oneOf ?? schema.anyOf
    if (branches.length < 2 || branches.length > 32) reject('snapshot_schema_unsupported')
    const matches = branches.filter(branch => branchMatches(value, branch, path, depth + 1)).length
    if ((schema.oneOf !== undefined && matches !== 1)
        || (schema.anyOf !== undefined && matches < 1)) {
      argumentFailure(path, schema.oneOf === undefined ? 'anyOf' : 'oneOf',
        schema.oneOf === undefined ? 'at_least_one_matching_branch' : 'exactly_one_matching_branch',
        `${path} does not satisfy its union schema`)
    }
    return
  }
  if (schema.enum !== undefined) {
    if (!Array.isArray(schema.enum) || schema.enum.length < 1
        || schema.enum.some(item => !jsonScalar(item))) reject('snapshot_schema_unsupported')
    if (!enumMatches(value, schema.enum)) argumentFailure(path, 'enum', schema.enum)
  }
  if (Object.hasOwn(schema, 'const') && !Object.is(value, schema.const)) {
    argumentFailure(path, 'const', schema.const)
  }
  if (schema.type === undefined) {
    if (schema.enum !== undefined || Object.hasOwn(schema, 'const')) return
    // The App uses an empty schema for explicitly unconstrained JSON values.
    if (Object.keys(schema).length === 0) {
      validatePortableJson(value, path)
      return
    }
    reject('snapshot_schema_unsupported', `Missing type at ${path}`)
  }
  if (schema.type === 'object') {
    plainRecord(value, 'arguments_schema', path)
    const properties = schema.properties ?? {}
    const required = schema.required ?? []
    plainRecord(properties, 'snapshot_schema_unsupported')
    if (!Array.isArray(required) || required.some(item => typeof item !== 'string')) {
      reject('snapshot_schema_unsupported')
    }
    if (schema.additionalProperties !== undefined
        && typeof schema.additionalProperties !== 'boolean') reject('snapshot_schema_unsupported')
    for (const name of required) {
      if (!Object.hasOwn(value, name)) argumentFailure(`${path}.${name}`, 'required', true)
    }
    for (const [name, child] of Object.entries(value)) {
      if (!Object.hasOwn(properties, name)) {
        if (schema.additionalProperties === false) {
          argumentFailure(`${path}.${name}`, 'additionalProperties', false)
        }
      } else {
        validateSchemaValue(child, properties[name], `${path}.${name}`, depth + 1)
      }
    }
    return
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value)) argumentFailure(path, 'type', 'array')
    if (schema.minItems !== undefined && value.length < schema.minItems) argumentFailure(path, 'minItems', schema.minItems)
    if (schema.maxItems !== undefined && value.length > schema.maxItems) argumentFailure(path, 'maxItems', schema.maxItems)
    if (schema.items !== undefined) {
      value.forEach((item, index) => validateSchemaValue(
        item, schema.items, `${path}[${index}]`, depth + 1,
      ))
    }
    return
  }
  if (schema.type === 'string') {
    if (typeof value !== 'string') argumentFailure(path, 'type', 'string')
    // JSON Schema counts Unicode code points; JavaScript String#length counts
    // UTF-16 code units and would disagree with the Python authority on astral
    // characters.
    const length = [...value].length
    if (schema.minLength !== undefined && length < schema.minLength) argumentFailure(path, 'minLength', schema.minLength)
    if (schema.maxLength !== undefined && length > schema.maxLength) argumentFailure(path, 'maxLength', schema.maxLength)
    return
  }
  if (schema.type === 'integer') {
    if (!Number.isSafeInteger(value)) argumentFailure(path, 'type', 'safe_integer')
  } else if (schema.type === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value)
        || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      argumentFailure(path, 'type', 'finite_json_number')
    }
  } else if (schema.type === 'boolean') {
    if (typeof value !== 'boolean') argumentFailure(path, 'type', 'boolean')
    return
  } else if (schema.type === 'null') {
    if (value !== null) argumentFailure(path, 'type', 'null')
    return
  } else if (schema.type === 'json') {
    validatePortableJson(value, path)
    return
  } else if (!['integer', 'number'].includes(schema.type)) {
    reject('snapshot_schema_unsupported', `Unsupported type at ${path}`)
  }
  for (const [keyword, compare] of [
    ['minimum', (a, b) => a < b],
    ['maximum', (a, b) => a > b],
    ['exclusiveMinimum', (a, b) => a <= b],
    ['exclusiveMaximum', (a, b) => a >= b],
  ]) {
    if (schema[keyword] !== undefined) {
      if (typeof schema[keyword] !== 'number' || !Number.isFinite(schema[keyword])) {
        reject('snapshot_schema_unsupported')
      }
      if (compare(value, schema[keyword])) argumentFailure(path, keyword, schema[keyword])
    }
  }
}

function inferEnumType(values, path) {
  const types = new Set(values.map(value => value === null ? 'null'
    : Number.isInteger(value) ? 'integer' : typeof value))
  if (types.size !== 1) reject('snapshot_schema_unsupported', `Mixed enum at ${path}`)
  const [type] = types
  if (!['string', 'integer', 'number', 'boolean', 'null'].includes(type)) {
    reject('snapshot_schema_unsupported', `Unsupported enum at ${path}`)
  }
  return type
}

function scalarMatchesType(type, value) {
  if (type === 'string') return typeof value === 'string'
  if (type === 'integer') return Number.isSafeInteger(value)
  if (type === 'number') {
    return typeof value === 'number' && Number.isFinite(value)
      && (!Number.isInteger(value) || Number.isSafeInteger(value))
  }
  if (type === 'boolean') return typeof value === 'boolean'
  if (type === 'null') return value === null
  return false
}

function validateNonNegativeBound(value, path) {
  if (!Number.isSafeInteger(value) || value < 0) {
    reject('snapshot_schema_unsupported', `${path} must be a non-negative safe integer`)
  }
}

function validateNumericBound(value, path) {
  if (typeof value !== 'number' || !Number.isFinite(value)
      || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
    reject('snapshot_schema_unsupported', `${path} must be a finite JSON number`)
  }
}

/**
 * Validate source-only constraints before projecting to the smaller DSH
 * author DSL.  DSH intentionally omits length/range keywords; Node therefore
 * keeps those constraints at the signed snapshot boundary and rejects every
 * source shape whose semantics cannot be represented plus rechecked here.
 */
function validateSourceSchema(schema, path) {
  assertSchemaObject(schema, path)
  const hasOneOf = Object.hasOwn(schema, 'oneOf')
  const hasAnyOf = Object.hasOwn(schema, 'anyOf')
  if (hasOneOf && hasAnyOf) {
    reject('snapshot_schema_unsupported', `${path} cannot contain oneOf and anyOf`)
  }
  if (hasOneOf || hasAnyOf) {
    for (const key of Object.keys(schema)) {
      if (key !== 'oneOf' && key !== 'anyOf' && !ANNOTATION_KEYS.has(key)) {
        reject('snapshot_schema_unsupported', `${path}.${key} is unsupported beside a union`)
      }
    }
    return undefined
  }

  let type = schema.type
  if (type === undefined && Array.isArray(schema.enum)) type = inferEnumType(schema.enum, path)
  if (type === undefined && Object.hasOwn(schema, 'const')) type = inferEnumType([schema.const], path)
  if (type === undefined && Object.keys(schema).every(key => ANNOTATION_KEYS.has(key))) {
    return 'json'
  }
  if (!SUPPORTED_SCHEMA_TYPES.has(type)) {
    reject('snapshot_schema_unsupported', `Unsupported type at ${path}`)
  }

  const allowed = {
    object: new Set(['type', 'properties', 'required', 'additionalProperties']),
    array: new Set(['type', 'items', 'minItems', 'maxItems']),
    string: new Set(['type', 'enum', 'const', 'minLength', 'maxLength']),
    integer: new Set([
      'type', 'enum', 'const', 'minimum', 'maximum',
      'exclusiveMinimum', 'exclusiveMaximum',
    ]),
    number: new Set([
      'type', 'enum', 'const', 'minimum', 'maximum',
      'exclusiveMinimum', 'exclusiveMaximum',
    ]),
    boolean: new Set(['type', 'enum', 'const']),
    null: new Set(['type', 'enum', 'const']),
    json: new Set(['type']),
  }[type]
  for (const key of Object.keys(schema)) {
    if (!ANNOTATION_KEYS.has(key) && !allowed.has(key)) {
      reject('snapshot_schema_unsupported', `${path}.${key} is unsupported on ${type}`)
    }
  }

  if (Object.hasOwn(schema, 'enum')) {
    if (!Array.isArray(schema.enum) || schema.enum.length < 1
        || schema.enum.some(value => !scalarMatchesType(type, value))) {
      reject('snapshot_schema_unsupported', `${path}.enum does not match ${type}`)
    }
  }
  if (Object.hasOwn(schema, 'const') && !scalarMatchesType(type, schema.const)) {
    reject('snapshot_schema_unsupported', `${path}.const does not match ${type}`)
  }
  if (Object.hasOwn(schema, 'enum') && Object.hasOwn(schema, 'const')
      && !schema.enum.some(value => Object.is(value, schema.const))) {
    reject('snapshot_schema_unsupported', `${path}.const is outside enum`)
  }

  for (const key of ['minLength', 'maxLength', 'minItems', 'maxItems']) {
    if (Object.hasOwn(schema, key)) validateNonNegativeBound(schema[key], `${path}.${key}`)
  }
  for (const key of ['minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum']) {
    if (Object.hasOwn(schema, key)) validateNumericBound(schema[key], `${path}.${key}`)
  }
  if (schema.minLength !== undefined && schema.maxLength !== undefined
      && schema.minLength > schema.maxLength) {
    reject('snapshot_schema_unsupported', `${path} has inverted string bounds`)
  }
  if (schema.minItems !== undefined && schema.maxItems !== undefined
      && schema.minItems > schema.maxItems) {
    reject('snapshot_schema_unsupported', `${path} has inverted array bounds`)
  }
  if (schema.minimum !== undefined && schema.maximum !== undefined
      && schema.minimum > schema.maximum) {
    reject('snapshot_schema_unsupported', `${path} has inverted numeric bounds`)
  }

  if (type === 'object') {
    const properties = schema.properties ?? {}
    const required = schema.required ?? []
    plainRecord(properties, 'snapshot_schema_unsupported')
    if (!Array.isArray(required) || required.some(item => typeof item !== 'string')
        || new Set(required).size !== required.length
        || required.some(name => !Object.hasOwn(properties, name))) {
      reject('snapshot_schema_unsupported', `${path}.required must name unique properties`)
    }
    if (schema.additionalProperties !== undefined
        && typeof schema.additionalProperties !== 'boolean') {
      reject('snapshot_schema_unsupported', `${path}.additionalProperties must be boolean`)
    }
  }
  return type
}

function annotations(schema, target) {
  for (const key of ['description', 'title', 'default', 'examples']) {
    if (Object.hasOwn(schema, key)) target[key] = structuredClone(schema[key])
  }
  const bounds = BOUND_KEYS.filter(key => Object.hasOwn(schema, key))
    .map(key => `${key}=${JSON.stringify(schema[key])}`)
  if (bounds.length > 0) {
    // The official DSH author DSL cannot express these JSON Schema keywords.
    // Keep its supported shape and disclose the unchanged execution bounds
    // through its public description annotation, including zero-valued bounds.
    const suffix = `App执行约束（此字段存在时）：${bounds.join('；')}。`
    if (target.description !== undefined && typeof target.description !== 'string') {
      reject('snapshot_schema_unsupported', 'description must be a string')
    }
    target.description = target.description === undefined ? suffix : `${target.description}\n${suffix}`
  }
  return target
}

function schemaToAuthorSpec(schema, path = 'schema', depth = 0) {
  if (depth > 32) reject('snapshot_schema_unsupported', `Schema too deep at ${path}`)
  const declaredType = validateSourceSchema(schema, path)
  if (Array.isArray(schema.oneOf) || Array.isArray(schema.anyOf)) {
    const branches = schema.oneOf ?? schema.anyOf
    if (branches.length < 2 || branches.length > 32) reject('snapshot_schema_unsupported')
    if (schema.anyOf !== undefined) {
      // Converting anyOf to the DSH exact-one DSL is only lossless when every
      // branch has a distinct declared root type (the App currently uses this
      // for object|null).  Ambiguous unions fail closed.
      const roots = branches.map((branch, index) => {
        assertSchemaObject(branch, `${path}.anyOf[${index}]`)
        return branch.type ?? (Array.isArray(branch.enum)
          ? inferEnumType(branch.enum, `${path}.anyOf[${index}]`) : undefined)
      })
      if (roots.some(root => root === undefined) || new Set(roots).size !== roots.length) {
        reject('snapshot_schema_unsupported', `Ambiguous anyOf at ${path}`)
      }
      if (roots.includes('json')
          || (roots.includes('integer') && roots.includes('number'))) {
        reject('snapshot_schema_unsupported', `Overlapping anyOf at ${path}`)
      }
    }
    return annotations(schema, {
      oneOf: branches.map((branch, index) => schemaToAuthorSpec(
        branch, `${path}.union[${index}]`, depth + 1,
      )),
    })
  }
  const type = declaredType
  const result = { type }
  if (Array.isArray(schema.enum)) result.enum = structuredClone(schema.enum)
  if (Object.hasOwn(schema, 'const')) result.const = structuredClone(schema.const)
  annotations(schema, result)
  if (type === 'array' && schema.items !== undefined) {
    result.items = schemaToAuthorSpec(schema.items, `${path}.items`, depth + 1)
  }
  if (type === 'object') {
    const properties = schema.properties ?? {}
    const required = new Set(schema.required ?? [])
    plainRecord(properties, 'snapshot_schema_unsupported')
    if ([...required].some(item => typeof item !== 'string')) reject('snapshot_schema_unsupported')
    result.additionalProperties = schema.additionalProperties !== false
    result.properties = {}
    for (const [name, child] of Object.entries(properties)) {
      const converted = schemaToAuthorSpec(child, `${path}.${name}`, depth + 1)
      if (required.has(name)) converted.required = true
      result.properties[name] = converted
    }
  }
  return result
}

function parameterSpecs(definition) {
  const schema = assertSchemaObject(definition.inputSchema, `${definition.name}.inputSchema`)
  if (schema.type !== 'object') reject('snapshot_schema_unsupported')
  // defineTool's parameter root is always closed.  Accepting an open App
  // schema here would silently narrow it, so require exact semantic parity.
  if (schema.additionalProperties !== false) {
    reject('snapshot_schema_unsupported', `${definition.name}.inputSchema must be closed`)
  }
  const converted = schemaToAuthorSpec(schema, `${definition.name}.inputSchema`)
  const result = converted.properties ?? {}
  try {
    // Exercise DSH's public compiler during snapshot admission, rather than
    // deferring an author-schema error until the turn registers its tools.
    parameterSchemaSpecToJsonSchema(result)
  } catch (error) {
    reject('snapshot_schema_unsupported', error instanceof Error ? error.message : undefined)
  }
  return result
}

export function validateNativeAppToolSchema(definition) { parameterSpecs(definition) }
/** Project the public ToolDefinition, never ToolRuntime's private serializer. */
export function nativeAppToolWireSchemas(definitions) {
  return definitions.map(({ name, description, parameters, deferLoading }) => ({
    name, description, parameters: structuredClone(parameters),
    ...(deferLoading === true ? { deferLoading } : {}),
  }))
}
export function validateNativeAppToolArguments(definition, args) {
  try {
    validateSchemaValue(args, definition.inputSchema)
    return canonicalDataJson(args)
  } catch (error) {
    if (!(error instanceof NativeAppToolSchemaError)
        || !['arguments_schema', 'arguments_json'].includes(error.code)) throw error
    const details = error.details ?? {
      path: 'arguments', constraint: 'portable_json', expected: 'finite_lossless_json',
    }
    const pointer = `/tools/${String(definition.name).replaceAll('~', '~0').replaceAll('/', '~1')}`
    const payload = {
      code: error.code, category: 'request_error', blocking_scope: 'request',
      message: error.message, ...details,
      app_call_admission: 'not_recorded', execution_state: 'did_not_execute',
      operation_may_have_completed: false, retryable: false,
      contract_ref: { resource: 'tool_contracts', pointer },
      recovery: { action: 'correct_arguments', resource: 'tool_contracts', pointer,
        retry_same_request: false },
    }
    // DSH preserves thrown errors as isError, but its public model-facing
    // channel renders Error.message rather than arbitrary custom properties.
    error.details = payload
    error.message = JSON.stringify(payload)
    throw error
  }
}
export function compileNativeAppToolDefinitions(definitions, invoke) {
  if (typeof invoke !== 'function') reject('tool_invoke_required')
  return Object.freeze(definitions.map(definition => defineTool({
    name: definition.name, description: definition.description,
    parameters: parameterSpecs(definition),
    output: { schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) {
      validateNativeAppToolArguments(definition, args)
      return invoke(definition, args, exec)
    },
  })))
}
